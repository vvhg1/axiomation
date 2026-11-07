# The OCR contract

axiomation reads a cited work's page with OCR when its text layer
does not serve it, and records on each page which engine read it:

- **A scanned page**, whatever its layer: a page that carries an image and
  shows a reader none of the work's own text. Its text is invisible (a
  scanner's OCR layer), painted under the image, or a stamp (a line drawn
  at one place on most of the document's pages: an archive's download
  line, a running head). A page whose invisible text outweighs what it
  shows is a scan too. So a scan carrying a publisher's or a library's old
  OCR layer is read again, and a typeset cover on a scanned article keeps
  its layer: the class is per page (`pdf.Pdf.page_kinds`).
- **A digital page that carries an image and no text.**
- **A digital page whose layer spells no words** (a font that lost its
  encoding): too few of its words in the system's word list, or too few
  words to judge in 200 characters or more, and Tesseract's reading of
  the page unlike the layer. A German page, an index or a page of
  formulas fails the word list and passes Tesseract, and keeps its
  layer.

Every other page keeps its layer; under a service, a digital page also
gains the text of its figures (§ "A figure's text"). Which engine reads a
page is the person's `ocr_backend`, in their user file
(`~/.config/axiomation/config.toml`, [the guide](guide.md#1-set-up) § 1):

<!-- closed set: axiomation.sources.config.OcrBackend -->
| backend | what it is | what it needs |
|---|---|---|
| `tesseract` | Tesseract in the ingest process; the default | the `tesseract` binary |
| `http` | any service that answers this contract | `ocr_url`: the endpoint |
| `paddle` | PaddleOCR-VL as PaddleX serves it (`paddlex --serve`) | `ocr_url`: its `/layout-parsing` endpoint |
| `mineru` | MinerU as its API server serves it (`mineru-kit api-server`) | `ocr_url`: its `/v1/tasks` endpoint |
| `chat` | a vision model behind an OpenAI-compatible chat-completions endpoint (a local server or a hosted provider) | `ocr_url`: its `/chat/completions` endpoint; `ocr_model` |

A remote service usually reads a scanned page better than Tesseract does.
`paddle` and `mineru` reach their engines' own servers as their projects
ship them, on the person's machine or elsewhere (§ "The engines' own
servers"); `chat` asks a vision model for the page's text (§ "A chat
model"); `http` reaches any other service that answers this page. No
backend has an address of its own: a service runs where its person runs
it.

The backend's name is written on each stored page: it says which engine
read it and decides which pages a re-ingest under another declared
backend replaces. Two different services both declared as `http` are
therefore one backend: after moving from one to the other, run
`axiomation sources ingest --force`.

Every ingest needs the `tesseract` binary, under any backend: the default
reads with it, and any other confirms with it a layer that may spell no
words ([the guide](guide.md#1-set-up) § 1 names its install);
`axiomation sources status` says whether it is found.

## The request

```text
POST <ocr_url>
Content-Type: application/octet-stream
Authorization: Bearer <ocr_api_key>   (only when the key is set)

<one page, rendered as a PNG at 300 dpi; or one figure cut from it>
```

`ocr_url` is the full endpoint, its path included
(`http://localhost:9000/parse`, `https://ocr.example.com/read`). Where
a service runs and the key that opens it are the person's, not the
paper's: `ocr_url` and `ocr_api_key` go in the user file, or
`AXIOMATION_OCR_URL` and `AXIOMATION_OCR_API_KEY` in the shell, never in
`axiomation.toml`.

A service reads each request alone: one page, never batched with others.
A page read in a batch reads differently by its neighbours, and the same
page must give the same text every time it is read, or what a
reader quoted from it may not be found on the next read.

## The answer

```text
200 OK
Content-Type: application/json

{"text": "the page, as text"}
```

Any other status, and any body that is not JSON with a `text` field, is a
failure.

`text` is the page in reading order, its blocks (a paragraph, a heading, a
formula, a caption) joined by a blank line, formulas in TeX between `$…$`,
`$$…$$`, `\(…\)` or `\[…\]`. An image set in the text (`<img …>`: MinerU
writes a picture in a table's cell as a `data:` URI) is cut: the page does
not print it.

A service whose model reads a page box by box sends its blocks too, each
with its box on the image it was sent, in pixels:

```json
{"text": "…", "blocks": [{"text": "BIBLIOGRAPHY", "box": [430, 240, 935, 287]}, …]}
```

Where it does, the page is its blocks, in their order, and `text` is not
read: the witness holds each block against what its own box shows (§ "The
witness"). A box is four numbers, left, top, right and bottom, with room
between them; a block of no text is skipped; fields beside `text` and
`box` are ignored. A box that is not four such numbers breaks the
contract. A service that sends no `blocks` is read from `text` alone, its
paragraphs blocks with no box.

## Putting a vendor API behind it

axiomation ships no vendor code. An adapter is a small service that
accepts the request above, calls the API it has a key for, and returns
the text:

```python
from flask import Flask, jsonify, request

app = Flask(__name__)


@app.post("/parse")
def parse():
    text = your_vendor_api(request.get_data())  # bytes in, text out
    return jsonify({"text": text})
```

Run it, and in your user file set `ocr_url` to its URL and
`ocr_backend = "http"`.

## The engines' own servers

`paddle` and `mineru` send the page and read the answer in their
servers' own shapes, and make of it the blocks of § "The answer": each
block's text and its box in the image's pixels. What follows is the
same under every backend.

- **`paddle`**: the page posted to PaddleX's `/layout-parsing` as JSON,
  `{"file": <the PNG in base64>, "fileType": 1}`; the blocks are the
  answer's `result.layoutParsingResults[].prunedResult.parsing_res_list`,
  each a `block_content` and a `block_bbox` in pixels, its formulas in
  `$…$` and `$$…$$`. A `formula_number` block is joined to the formula
  before it, its box to the formula's: the pipeline's own Markdown drops
  it, and a reader quotes "(2.2)" with its formula. An `errorCode` other
  than 0 is a failure.
- **`mineru`**: a task created at `/v1/tasks` for the one page (its
  name, size and SHA-256), the page uploaded where the task says (no
  upload where the server holds the page already), the task polled at
  `/v1/tasks/<id>` until it is `completed`, and its result, a zip, read:
  the blocks of `structured_content.json`'s page, each box in shares of
  the image's width and height. A display equation comes bare and is set
  between `\[ \]`; a table's, a picture's or a chart's captions and
  footnotes are blocks of their own, read with it in the order of their
  tops. The server's default tier (`standard`) is read, as the server
  chooses it; a task that ends otherwise than `completed` is a failure.
  The key goes to the server's own addresses only: an upload address
  elsewhere is a store's, signed already.

### Running one

Each engine's server is its project's, installed and started as its
project documents it; axiomation runs none. The addresses below are the
servers' default ports on the person's machine:

| backend | the server | `ocr_url` |
|---|---|---|
| `paddle` | [PaddleOCR-VL](https://github.com/PaddlePaddle/PaddleOCR) behind PaddleX's serving (`paddlex --serve`, the `PaddleOCR-VL` pipeline) | `http://localhost:8080/layout-parsing` |
| `mineru` | [MinerU](https://github.com/opendatalab/MinerU)'s API server (`mineru-kit api-server`) | `http://localhost:8000/v1/tasks` |

Checked: PaddleOCR-VL 1.6, its VLM on vLLM or, under PaddleX 3.7.2, on
`mlx_vlm.server` 0.6.17; MinerU 4.1.3 on llama.cpp and on its MLX engine,
with and without `--api-key`.
What their own documentation does not say:

- PaddleX sends a page's blocks to the VLM server at once. A newer
  `mlx_vlm.server` (0.7.12) batches them and fails on some pages with an
  error 500, unless it runs with `--max-num-seqs 1`; 0.6.17 reads them.
- MinerU's MLX engine (`mineru-kit vlm-server --engine mlx`) lists every
  MLX model in the Hugging Face cache, and `api-server` refuses a page
  while it sees more than one: name the model (`--vlm-model
  opendatalab/MinerU2.5-Pro-2605-1.2B`).

## A chat model

`chat` sends the page, as an image, to a vision model behind an
OpenAI-compatible chat-completions endpoint, with one prompt for every
model: the page's text in reading
order, as Markdown, formulas in LaTeX, nothing that is not printed. The
answer's `choices[0].message.content` is the page, a code fence around
it taken off; its paragraphs are its blocks, with no box, so the witness
holds them against the page as a whole (§ "The witness"), and no block
is read alone.

```toml
ocr_backend = "chat"
ocr_url = "https://openrouter.ai/api/v1/chat/completions"
ocr_model = "google/gemini-3.8-flash"     # the service's name for it
ocr_api_key = "…"                         # the provider's key
```

A local server that speaks this API takes its own
`/v1/chat/completions` address and its name for the model; of these,
`mlx_vlm.server` was checked (below), Ollama, LM Studio and vLLM not.

Checked: Qwen2.5-VL-7B (4-bit MLX) on `mlx_vlm.server` 0.6.17, and
`google/gemini-3.8-flash` through OpenRouter. A provider's refusal (a model the
account may not use) stops the ingest with the provider's reason.

The page records `chat`, not the model: after moving to another model,
run `axiomation sources ingest --force`. A model trained to read a page
block by block (PaddleOCR-VL, MinerU) reads better through its own
server (`paddle`, `mineru`): given a whole page, PaddleOCR-VL loops.

## How the ingest uses it (for contributors)

The ingest's side of the contract, for a contributor: a service needs
none of it.

### What the ingest does with each page

- One request per page that needs OCR. The next scanned page is sent
  while Tesseract witnesses this one, so a service may hold two requests
  at once; it answers each alone, in turn.
- 300 seconds for a request (for `mineru`, the whole task), and one
  repeat after a transport failure, an error status or a MinerU task that
  failed. An answer of the wrong shape is not repeated.
- A failure stops the ingest with the fix named, at the first page that
  needs the service: a paper of digital pages needs none. The works not
  yet read keep their stored pages, and a work being read again is
  replaced only once its new pages are read. A failure while the engines
  still read a blank page sent after it (one page that timed out, one
  Tesseract cannot read) is that work's alone: it keeps its stored pages,
  is reported as failed, and the run goes on. It never falls back to another engine: a
  quiet fallback would poison a corpus in a way nobody notices until a
  judgment comes back wrong.
- An empty text records the page as `failed` without failing the work;
  `axiomation sources ingest --force` reads it again. A short text (a
  dedication, a part title) is a page that read.
- Each page records what read it (`ocr_backend`: the backend's name, or
  `none` for a page kept as its layer), and `axiomation sources status`
  counts the pages each read. The collection records none: a corpus is
  read by the backends of every run that wrote to it.

### The witness

A vision-language model writes fluent text, so its faults read like text:
a paragraph made up, a sentence or a reference written twice, a formula
looped, a digit added to a label, text left out. Under any backend but
`tesseract`, each page the service reads is held against two witnesses
independent of its model: Tesseract's reading of the same image and, on a
scan, its old OCR layer; a block with a box also against Tesseract's
reading of its box alone. Under `tesseract` the witness would be held
against itself, and none runs. The thresholds, and why each is where it
is, are `ingest/witness.py`'s.

- **What is checked:** each paragraph of the answer's blocks against its
  witnesses; a label line's number (Theorem, Lemma, Section and its
  number); a stretch written twice that no witness holds twice; a short
  sequence written many times in a row; text a witness holds that the
  reading leaves out; a block whose text has no room in its box, which is
  dropped; a block whose box shows no word, which is kept and found. A
  witness writes no TeX: a formula counts for its room, never its words.
- **A second request.** A page with a finding is rendered again at a
  slightly smaller scale and read once more, then once more at a smaller
  one. The first reading with no finding is the page's text; readings of
  a page are never joined.
- **A block read alone.** Where no reading settles the page, each block
  of the likeliest reading (no repeat or loop, then the one its witnesses
  hold most) that carries a finding and a box is cropped
  from the page and read alone, and replaces the block where its
  witnesses hold it.
- **Disputed.** Where nothing settles the page, the likeliest reading
  stands and the page records how many findings it carries
  (`ocr_disputed`). A witness's text never enters a page. `sources page`
  names a disputed page, and the quotation check leaves any difference on
  it to a reader, who reads the page as an image.
- **The record.** Every page the service read records `ocr_disputed`, 0
  where nothing was left, and the version of the witness that judged it
  (`ocr_reading`). A page a witness judged keeps its text and is not held
  again. A page the service read that the installed witness has not judged is
  listed (`unwitnessed`): `sources ingest --reread` reads afresh one an
  older witness left disputed, and holds the rest against the witnesses,
  reading again only a page it finds a fault in.

### A figure's text

A digital page's layer holds no word a figure draws as pixels: a plot's
legend, a diagram's sentences, a cover's title. Under any backend but
`tesseract`, a digital page kept as its layer that carries a raster image
is read by Tesseract, and an image holding words the layer lacks (a few
words Tesseract reads with confidence, each a word of the word list or
of the work; `ingest/figures.py`) is cut from the page and sent to the
service. Its answer is added after the layer, under the line `[text in a
figure, read by OCR]`. Where the service answers nothing (a chart), or
its answer has no room in the figure's box, the figure keeps Tesseract's
lines inside its box that hold such a word. The page's `ocr_backend`
stays `none`: the line is the record that OCR wrote what follows it. The page
records how many figures gave text (`ocr_figures`), 0 for none. A
stored layer page that records no count is tested at the next ingest:
one with no image is recorded as tested, its text untouched; a work
with such a page that carries an image is listed (`untested`), and
`sources ingest --reread` reads its figures.

### A layer's text

A page kept as its layer holds the PDF's own text as PDFium reads it
(`pdf.Pdf.text`). A font with no Unicode map still names its glyphs, and
PDFium maps a name only by Adobe's list, so a TeX symbol font's `∑`
would come back as `X`. Each character PDFium cannot map takes the
character its glyph's name stands for, by Adobe's and TeX's glyph lists
and the names fonts set for TeX give (`glyphs.py`); a name no list knows
leaves PDFium's code. Each page kept as its layer records the version of
the reading that wrote it (`layer_reader`): at the next ingest, a page
an earlier version wrote that reads alike takes the version, its text
untouched; a work with a page that reads otherwise is listed
(`outdated`), and `sources ingest --reread` reads it again. A scan's
page kept as its layer is never refreshed as a layer, and under the
default backend a work whose other pages need OCR is listed only: only
a declared backend reads such a page again. A page read
again lapses nothing, a judgment resting on its work's file; `--dry-run`
first names what each cause reads again.
