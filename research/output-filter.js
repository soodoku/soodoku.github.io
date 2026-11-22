(() => {
  const type = new URLSearchParams(location.search).get("type");
  if (!type) return;
  const entries = [...document.querySelectorAll("[data-kind]")];
  if (!entries.some(entry => entry.dataset.kind === type)) return;

  for (const entry of entries) {
    entry.hidden = entry.dataset.kind !== type;
  }
  for (const group of document.querySelectorAll("#research .category-toggle")) {
    group.hidden = !group.querySelector("[data-kind]:not([hidden])");
    group.open = !group.hidden;
  }
  for (const link of document.querySelectorAll("[data-catalog-view]")) {
    const url = new URL(link.href);
    url.searchParams.set("type", type);
    link.href = url.href;
  }
})();
