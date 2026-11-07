// The theme the page draws in, named before the palette loads
// (`palette.THEMES`): the address's `theme` (`?theme=light` or `dark`),
// else the browser's light or dark, followed when it changes. The page
// keeps its state in its address, so a reload keeps it.
(function () {
  "use strict";
  const NAMES = { light: "paper", dark: "graphite" };
  const asked = NAMES[new URLSearchParams(location.search).get("theme")];
  const light = window.matchMedia("(prefers-color-scheme: light)");
  const theme = asked || (light.matches ? "paper" : "graphite");
  (window.AxView = window.AxView || {}).theme = theme;
  document.documentElement.dataset.theme = theme;
  if (!asked) light.addEventListener("change", () => location.reload());
})();
