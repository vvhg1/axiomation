"""Reading one page image.

`extract.py` decides which pages need OCR and renders them; this module
decides how one image becomes text. The split is by responsibility, and it
keeps a service's wire shape out of the page loop.

Tesseract runs in this process and needs one system binary. A service is
reached over HTTP, in one of three shapes: `http` posts the page to any
service that answers the contract in `docs/ocr.md`; `paddle` and `mineru`
speak their engines' own servers as their projects ship them (PaddleX's
`/layout-parsing`, MinerU's `/v1/tasks`), so a person runs either as it
comes; `chat` asks a vision model behind an OpenAI-compatible
chat-completions endpoint for the page's text. Every client gives the
page as its blocks, each with its box where the service gives one.

The backend's name is written to the payload field `ocr_backend`: it says
which engine read the page and decides what a re-ingest under another
backend replaces. Two different services both declared as `http` are
therefore one value to `stale_backends`: a user who moves to another
service must run `ingest --force`.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from axiomation.sources import tesseract
from axiomation.sources.config import (
    OcrBackend,
    ResolvedSources,
    SourcesError,
)
from axiomation.sources.contract import TextSource
from axiomation.user_settings import UserKey, where_to_put

#: Where a reading sent without boxes divides into its blocks.
_PARAGRAPH_RE = re.compile(r"\n\s*\n")
#: An image a service sets inside its text: MinerU writes a picture in a
#: table's cell as `<img src="data:image/jpeg;base64,…">`, tens of thousands
#: of characters the page does not print.
_IMAGE_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)

#: How long one page may take, and how many times a failed POST is repeated.
#: A page of a scanned book takes a vision model tens of seconds.
OCR_TIMEOUT = 300
OCR_RETRIES = 1
#: How often a MinerU task is asked whether it is done: it reads a page in
#: one to four seconds.
MINERU_POLL_SECONDS = 0.5


class OcrError(SourcesError):
    """The OCR engine could not read a page. It stops an ingest: every page
    after it would fail the same way, and a work read again would lose the
    pages it had."""


class OcrProvider(Protocol):
    """How one rendered page becomes text."""

    #: The value written to the payload field `ocr_backend`.
    name: TextSource

    def read(self, png: bytes) -> str:
        """The text of one page. Raises `OcrError` when it cannot."""
        ...


@dataclass(frozen=True, slots=True)
class Block:
    """One block of a page a service read: its text, and its box on the
    image it read (left, top, right, bottom, in the image's pixels), None
    where the reading came without one."""

    text: str
    box: tuple[int, int, int, int] | None = None


@runtime_checkable
class BlockReader(OcrProvider, Protocol):
    """A provider whose reading of a page comes as its blocks, each with
    its box where the service gives one (`docs/ocr.md` § The answer)."""

    def read_blocks(self, png: bytes) -> tuple[Block, ...]:
        """One page's blocks, in reading order. Raises `OcrError` when it
        cannot."""
        ...


@dataclass(frozen=True, slots=True)
class Word:
    """One word Tesseract read, where it stands on the image, and how sure
    it was."""

    text: str
    #: left, top, right, bottom, in the image's pixels
    box: tuple[int, int, int, int]
    #: Tesseract's confidence, 0 to 100
    confidence: float
    #: the line it stands on, as Tesseract numbers its blocks, paragraphs
    #: and lines
    line: tuple[int, int, int]

    @property
    def centre(self) -> tuple[float, float]:
        left, top, right, bottom = self.box
        return (left + right) / 2, (top + bottom) / 2


class Witness(OcrProvider, Protocol):
    """Tesseract as another reader's witness: its text of a page, and its
    words with their places (`figures.py`)."""

    def words(self, png: bytes) -> list[Word]:
        """Every word on one page, in reading order."""
        ...


@dataclass(frozen=True, slots=True)
class TesseractProvider:
    """Tesseract in this process. Needs the `tesseract` binary."""

    name: TextSource = TextSource.TESSERACT

    def read(self, png: bytes) -> str:
        import pytesseract
        from PIL import Image

        with Image.open(io.BytesIO(png)) as image:
            try:
                return pytesseract.image_to_string(image).strip()
            except (pytesseract.TesseractError, OSError) as error:
                raise OcrError(f"Tesseract could not read a page ({error}).") from None

    def words(self, png: bytes) -> list[Word]:
        import pytesseract
        from PIL import Image

        with Image.open(io.BytesIO(png)) as image:
            try:
                data = pytesseract.image_to_data(
                    image, output_type=pytesseract.Output.DICT
                )
            except (pytesseract.TesseractError, OSError) as error:
                raise OcrError(f"Tesseract could not read a page ({error}).") from None
        return [
            Word(
                data["text"][n],
                (
                    data["left"][n],
                    data["top"][n],
                    data["left"][n] + data["width"][n],
                    data["top"][n] + data["height"][n],
                ),
                float(data["conf"][n]),
                (data["block_num"][n], data["par_num"][n], data["line_num"][n]),
            )
            for n in range(len(data["text"]))
            if data["text"][n].strip()
        ]

    def check(self) -> None:
        if why := tesseract.missing():
            raise OcrError(why)


@dataclass(frozen=True, slots=True)
class HttpProvider:
    """Any service that answers the contract in `docs/ocr.md`.

    Fails loudly when the service is unreachable. Degrading quietly to
    another engine would poison a corpus build in a way nobody would notice
    until a verdict came back wrong.
    """

    url: str
    name: TextSource = TextSource.HTTP
    api_key: str = ""

    def read(self, png: bytes) -> str:
        return _joined(self.read_blocks(png))

    def read_blocks(self, png: bytes) -> tuple[Block, ...]:
        body = _repeated(
            self.url,
            lambda: _request(
                self.url,
                data=png,
                headers={"Content-Type": "application/octet-stream"},
                api_key=self.api_key,
            ),
        )
        return _answered(_contract_blocks, body, self.url, _CONTRACT_SHAPE)


@dataclass(frozen=True, slots=True)
class PaddleProvider:
    """PaddleOCR-VL as PaddleX serves it (`paddlex --serve`): the page
    posted to `/layout-parsing`, its blocks read from the layout's parsing
    results, each with its box in the image's pixels."""

    url: str
    name: TextSource = TextSource.PADDLE
    api_key: str = ""

    def read(self, png: bytes) -> str:
        return _joined(self.read_blocks(png))

    def read_blocks(self, png: bytes) -> tuple[Block, ...]:
        request = json.dumps(
            {"file": base64.b64encode(png).decode("ascii"), "fileType": 1}
        ).encode()
        body = _repeated(
            self.url,
            lambda: _request(
                self.url,
                data=request,
                headers={"Content-Type": "application/json"},
                api_key=self.api_key,
            ),
        )
        return _answered(_paddle_blocks, body, self.url, _PADDLE_SHAPE)


@dataclass(frozen=True, slots=True)
class MineruProvider:
    """MinerU as its API server serves it (`mineru-kit api-server`): a task
    created at `url`, the page uploaded to it, the task polled until it is
    done, and the page's blocks read from its result's
    `structured_content.json`.

    The page goes in a task of its own: a page read in a batch reads
    differently by its neighbours (`docs/ocr.md` § The request).
    """

    url: str
    name: TextSource = TextSource.MINERU
    api_key: str = ""

    def read(self, png: bytes) -> str:
        return _joined(self.read_blocks(png))

    def read_blocks(self, png: bytes) -> tuple[Block, ...]:
        try:
            result = _repeated(self.url, lambda: self._result(png))
        except (KeyError, IndexError, TypeError, AttributeError) as error:
            raise OcrError(
                f"the OCR service at {self.url} did not answer {_MINERU_SHAPE} "
                f"({type(error).__name__}: {error})."
            ) from None
        return _answered(
            lambda body: _mineru_blocks(body, _image_size(png)),
            result,
            self.url,
            _MINERU_SHAPE,
        )

    def _result(self, png: bytes) -> bytes:
        """The task's result: the zip of one page's reading."""
        deadline = time.monotonic() + OCR_TIMEOUT
        created = _json_of(
            _request(
                self.url,
                data=json.dumps(
                    {
                        "output_formats": ["default"],
                        "files": [
                            {
                                "file_name": "page.png",
                                "bytes": len(png),
                                "sha256sum": hashlib.sha256(png).hexdigest(),
                            }
                        ],
                    }
                ).encode(),
                headers={"Content-Type": "application/json"},
                api_key=self.api_key,
                deadline=deadline,
            ),
            self.url,
        )
        task_url = f"{self.url.rstrip('/')}/{created['task_id']}"
        # The server keeps a page by its hash and asks for no upload of one
        # it holds.
        for file in created["files"]:
            if upload := file.get("upload"):
                _request(
                    self._address(upload["url"]),
                    data=png,
                    headers=dict(upload.get("headers") or {}),
                    method="PUT",
                    api_key=self._key_for(upload["url"]),
                    deadline=deadline,
                )
        while True:
            task = _json_of(
                _request(task_url, api_key=self.api_key, deadline=deadline),
                self.url,
            )
            status = str(task["status"])
            if status in _MINERU_DONE:
                break
            if status in _MINERU_ENDED or (
                task.get("finished_at") and status != "running"
            ):
                raise _ServiceFailed(f"MinerU ended the task {status}: {_why(task)}")
            if time.monotonic() > deadline:
                raise TimeoutError(f"the task was not done in {OCR_TIMEOUT} s")
            time.sleep(MINERU_POLL_SECONDS)
        result_url = task["files"][0]["result_url"]
        return _request(
            self._address(result_url),
            api_key=self._key_for(result_url),
            deadline=deadline,
        )

    def _address(self, url: str) -> str:
        """An address the server gave, which may be a path on itself."""
        return urllib.parse.urljoin(self.url, url)

    def _key_for(self, url: str) -> str:
        """The key, for the server's own addresses only: an upload address
        may be a store's, signed already, which a second key breaks."""
        mine = urllib.parse.urlsplit(self.url)
        theirs = urllib.parse.urlsplit(self._address(url))
        same = (theirs.scheme, theirs.netloc) == (mine.scheme, mine.netloc)
        return self.api_key if same else ""


@dataclass(frozen=True, slots=True)
class ChatProvider:
    """A vision model behind an OpenAI-compatible chat-completions endpoint
    (a local server or a hosted provider): the page sent as an image
    with `CHAT_PROMPT`, its answer the page's Markdown. A chat model draws
    no boxes, so its blocks are its paragraphs, held by the witness against
    the page as a whole."""

    url: str
    model: str
    name: TextSource = TextSource.CHAT
    api_key: str = ""

    def read(self, png: bytes) -> str:
        return _joined(self.read_blocks(png))

    def read_blocks(self, png: bytes) -> tuple[Block, ...]:
        image = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        request = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "max_tokens": CHAT_MAX_TOKENS,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": image}},
                            {"type": "text", "text": CHAT_PROMPT},
                        ],
                    }
                ],
            }
        ).encode()
        body = _repeated(
            self.url,
            lambda: _request(
                self.url,
                data=request,
                headers={"Content-Type": "application/json"},
                api_key=self.api_key,
            ),
        )
        return _answered(_chat_blocks, body, self.url, _CHAT_SHAPE)


#: What a chat model is asked. One prompt for every model: a model of its
#: own prompt (PaddleOCR-VL's "OCR:") reads better through its own server.
CHAT_PROMPT = (
    "Transcribe all the text printed on this page, in reading order, as "
    "Markdown, with a blank line between blocks (a paragraph, a heading, a "
    "caption, a formula, a table). Write each formula in LaTeX between "
    "\\( \\) inline or \\[ \\] displayed, and a table as a Markdown table. "
    "Write only what is printed: no description of a picture, no comment, "
    "no preamble."
)
#: Room for the densest page; a model that loops on stops here, and the
#: witness finds the loop.
CHAT_MAX_TOKENS = 8192

#: The task states MinerU ends a task in that read it.
_MINERU_DONE = frozenset({"completed", "succeeded"})
#: The task states MinerU ends a task in that did not read it.
_MINERU_ENDED = frozenset({"failed", "canceled", "cancelled", "partial"})

_CONTRACT_SHAPE = (
    "the axiomation OCR contract (docs/ocr.md): expected JSON with a "
    '"text" field, and "blocks" of a "text" and a "box" each where it sends '
    "them"
)
_PADDLE_SHAPE = (
    "PaddleX's /layout-parsing answer: expected JSON whose "
    '"result.layoutParsingResults" hold a "prunedResult.parsing_res_list" '
    "of blocks; is ocr_url the server's /layout-parsing endpoint?"
)
_CHAT_SHAPE = (
    "an OpenAI-compatible chat completion: expected JSON whose "
    '"choices[0].message.content" holds the text; is ocr_url the '
    "service's /chat/completions endpoint, and ocr_model a model it serves?"
)
_MINERU_SHAPE = (
    "MinerU's /v1/tasks answer: expected a task whose result is a zip "
    'holding "structured_content.json"; is ocr_url the server\'s /v1/tasks '
    "endpoint?"
)


#: How much of a service's reason for an error status an error quotes.
_REASON_CHARS = 400


class _ServiceFailed(Exception):
    """The service answered that it could not read the page, an error
    status among them: repeated once."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        #: the HTTP status, where an error status said it
        self.status = status


def _request(
    url: str,
    *,
    data: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    method: str | None = None,
    api_key: str = "",
    deadline: float | None = None,
) -> bytes:
    """One HTTP exchange's body; an error status raises."""
    sent = dict(headers or {})
    timeout = OCR_TIMEOUT
    if deadline is not None:
        timeout = max(1, round(deadline - time.monotonic()))
    request = urllib.request.Request(url, data=data, headers=sent, method=method)
    if api_key:
        # Unredirected: a redirect elsewhere must not carry the key.
        request.add_unredirected_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        # The service's own reason is what a person acts on: a provider
        # that will not serve the model, a key it refuses.
        said = error.read().decode("utf-8", "replace").strip()
        raise _ServiceFailed(
            f"HTTP {error.code}: {said[:_REASON_CHARS] or error.reason}",
            status=error.code,
        ) from None


def _repeated[T](url: str, exchange: Callable[[], T]) -> T:
    """`exchange`, repeated once after a transport failure or an error
    status; a second failure stops the ingest with the fix named."""
    last_error: Exception | None = None
    for _ in range(OCR_RETRIES + 1):
        try:
            return exchange()
        except (urllib.error.URLError, TimeoutError, OSError, _ServiceFailed) as error:
            last_error = error
    if isinstance(last_error, _ServiceFailed) and last_error.status in (401, 403):
        raise OcrError(
            f"OCR through {url} was refused ({last_error}). Give the key the "
            f"service asks for: {where_to_put(UserKey.OCR_API_KEY)}."
        )
    raise OcrError(
        f"OCR through {url} failed ({last_error}). Start the service, "
        "point ocr_url at its endpoint (docs/ocr.md): "
        f"{where_to_put(UserKey.OCR_URL)}; or set "
        'ocr_backend = "tesseract" to read pages locally.'
    )


def _answered(
    blocks_of: Callable[[bytes], tuple[Block, ...]], body: bytes, url: str, shape: str
) -> tuple[Block, ...]:
    """A service's answer as its blocks; an answer of another shape names
    the shape it should have had. It is not repeated: it would come again."""
    try:
        return blocks_of(body)
    except (
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        zipfile.BadZipFile,
    ) as error:
        raise OcrError(
            f"the OCR service at {url} did not answer {shape} ({error})."
        ) from None


def _json_of(body: bytes, url: str) -> Any:
    try:
        return json.loads(body)
    except ValueError as error:
        raise OcrError(
            f"the OCR service at {url} did not answer {_MINERU_SHAPE} ({error})."
        ) from None


def _why(task: Mapping[str, Any]) -> str:
    """What a failed MinerU task says of itself, where it says anything."""
    said = [task.get("error")]
    said += [file.get("error") for file in task.get("files") or []]
    return "; ".join(str(s) for s in said if s)[:300] or "no reason given"


def _joined(blocks: tuple[Block, ...]) -> str:
    return "\n\n".join(block.text for block in blocks)


def provider_for(settings: ResolvedSources, *, api_key: str = "") -> OcrProvider:
    """The provider one run reads pages with.

    Built once, before the first page, so a backend that cannot work says so
    at the start of an ingest: Tesseract's binary is looked for, a service's
    address must be set. A service that is down is met at the first page
    that needs it, and that failure stops the run before its work is
    written (`run.apply_ingest`): a paper of digital pages needs no service.
    """
    provider = _provider(settings, api_key)
    if isinstance(provider, TesseractProvider):
        provider.check()
    return provider


def tesseract_provider() -> TesseractProvider:
    """Tesseract, checked: what confirms a layer that may spell no words,
    under any backend."""
    provider = TesseractProvider()
    provider.check()
    return provider


def _provider(settings: ResolvedSources, api_key: str) -> OcrProvider:
    if settings.ocr_backend is OcrBackend.TESSERACT:
        return TesseractProvider()
    if not settings.ocr_url:
        raise SourcesError(
            f'ocr_backend = "{settings.ocr_backend.value}" needs an address: '
            f"{where_to_put(UserKey.OCR_URL)}; or set the backend to "
            '"tesseract" to read pages locally.'
        )
    if settings.ocr_backend is OcrBackend.PADDLE:
        return PaddleProvider(url=settings.ocr_url, api_key=api_key)
    if settings.ocr_backend is OcrBackend.MINERU:
        return MineruProvider(url=settings.ocr_url, api_key=api_key)
    if settings.ocr_backend is OcrBackend.CHAT:
        if not settings.ocr_model:
            raise SourcesError(
                'ocr_backend = "chat" needs the model to read with: '
                f"{where_to_put(UserKey.OCR_MODEL)}."
            )
        return ChatProvider(
            url=settings.ocr_url, model=settings.ocr_model, api_key=api_key
        )
    return HttpProvider(url=settings.ocr_url, api_key=api_key)


def _contract_blocks(body: bytes) -> tuple[Block, ...]:
    """A contract answer's blocks: its `blocks` where it sends them, each
    with its box, else its `text` cut at its blank lines, with none; an
    image set in a text cut."""
    answer = json.loads(body)
    text = _IMAGE_RE.sub("", str(answer["text"]))
    sent = answer.get("blocks")
    if sent is None:
        return tuple(
            Block(part.strip()) for part in _PARAGRAPH_RE.split(text) if part.strip()
        )
    said = [(_IMAGE_RE.sub("", str(block["text"] or "")), block) for block in sent]
    return tuple(
        Block(written.strip(), _box_of(block["box"]))
        for written, block in said
        if written.strip()
    )


def _paddle_blocks(body: bytes) -> tuple[Block, ...]:
    """PaddleX's blocks in reading order. A formula's number comes as a block
    of its own after it, and is joined to it, its box to the formula's: the
    pipeline's own text drops it, and a reader quotes "(2.16)" with its
    formula."""
    answer = json.loads(body)
    if answer.get("errorCode", 0) != 0:
        raise ValueError(f"error {answer['errorCode']}: {answer.get('errorMsg')}")
    blocks: list[Block] = []
    for page in answer["result"]["layoutParsingResults"]:
        for found in page["prunedResult"]["parsing_res_list"]:
            text = _IMAGE_RE.sub("", str(found.get("block_content") or "")).strip()
            if not text:
                continue
            box = _box_of(found["block_bbox"])
            if found.get("block_label") == "formula_number" and blocks:
                formula = blocks.pop()
                assert formula.box is not None
                blocks.append(Block(f"{formula.text} {text}", _union(formula.box, box)))
            else:
                blocks.append(Block(text, box))
    return tuple(blocks)


def _mineru_blocks(zipped: bytes, size: tuple[int, int]) -> tuple[Block, ...]:
    """MinerU's blocks of one page in reading order, each box from shares of
    the image's width and height to its pixels. A table's, a picture's or a
    chart's captions and footnotes are blocks of their own, read with it in
    the order of their tops; a display equation comes bare and is set
    between `\\[ \\]`."""
    with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
        structured = json.loads(archive.read("structured_content.json"))
    pages = structured["pages"]
    found = pages[0]["blocks"] if pages else []
    blocks: list[Block] = []
    for block in found:
        group = [block, *(block.get("captions") or []), *(block.get("footnotes") or [])]
        for part in sorted(group, key=lambda part: float(part["bbox"][1])):
            text = _IMAGE_RE.sub("", str(part.get("content") or "")).strip()
            if not text:
                continue
            if part.get("type") == "equation" and not _FORMULA_OPEN_RE.match(text):
                text = f"\\[ {text} \\]"
            blocks.append(Block(text, _scaled(part["bbox"], size)))
    return tuple(blocks)


def _chat_blocks(body: bytes) -> tuple[Block, ...]:
    """A chat model's answer as its paragraphs, with no box. A model that
    wraps the page in a code fence has the fence taken off."""
    answer = json.loads(body)
    text = answer["choices"][0]["message"]["content"]
    if not isinstance(text, str):
        raise TypeError(f"content is {type(text).__name__}, not text")
    if fenced := _FENCE_RE.fullmatch(text.strip()):
        text = fenced.group(1)
    text = _IMAGE_RE.sub("", text)
    return tuple(
        Block(part.strip()) for part in _PARAGRAPH_RE.split(text) if part.strip()
    )


#: A whole answer between a code fence's marks.
_FENCE_RE = re.compile(r"```[A-Za-z]*\n(.*?)\n?```", re.DOTALL)
#: A formula the service set between delimiters itself.
_FORMULA_OPEN_RE = re.compile(r"\$|\\\[|\\begin\{")


def _box_of(box: list[float]) -> tuple[int, int, int, int]:
    left, top, right, bottom = (round(float(edge)) for edge in box)
    if not (left < right and top < bottom):
        raise ValueError(f"a box of no area: {box}")
    return left, top, right, bottom


def _union(
    one: tuple[int, int, int, int], other: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    return (
        min(one[0], other[0]),
        min(one[1], other[1]),
        max(one[2], other[2]),
        max(one[3], other[3]),
    )


def _scaled(shares: list[float], size: tuple[int, int]) -> tuple[int, int, int, int]:
    """A box in shares of the image's width and height, in its pixels: one
    pixel wide and high at least, as a box the service drew."""
    width, height = size
    left, top, right, bottom = (float(share) for share in shares)
    x0, y0 = round(left * width), round(top * height)
    return _box_of(
        [x0, y0, max(round(right * width), x0 + 1), max(round(bottom * height), y0 + 1)]
    )


def _image_size(png: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        return image.size
