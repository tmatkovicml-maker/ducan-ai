// "Kopiraj" buttons: copy the text of the nearest [data-copy] element.
document.addEventListener("click", async (event) => {
  const button = event.target.closest("button.copy");
  if (!button) return;
  const source = button.closest(".copyable")?.querySelector("[data-copy]");
  if (!source) return;
  try {
    await navigator.clipboard.writeText(source.innerText.trim());
    button.textContent = "Kopirano";
  } catch {
    const range = document.createRange();
    range.selectNodeContents(source);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    button.textContent = "Označeno, kopiraj ručno";
  }
  setTimeout(() => { button.textContent = "Kopiraj"; }, 2500);
});
