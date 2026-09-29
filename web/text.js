/* Pure helpers shared by the UI and Node tests. No runtime dependencies. */
(function (root) {
  function splitForSpeech(text, max = 60) {
    const out = []; let part = "";
    for (const char of Array.from(text.trim())) {
      part += char;
      if (/[。！？!?；;\n]/u.test(char) || Array.from(part).length >= max) {
        if (/[\p{L}\p{N}]/u.test(part)) out.push(part.trim());
        part = "";
      }
    }
    if (/[\p{L}\p{N}]/u.test(part)) out.push(part.trim());
    return out;
  }
  function ordered(items) { return [...items.values()].sort((a,b) => a.run-b.run || a.id-b.id); }
  function toPlainText(items) { return ordered(items).map(x=>x.text).join("\n"); }
  const api = {splitForSpeech, ordered, toPlainText};
  root.TingjianText = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(globalThis);
