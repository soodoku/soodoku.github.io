(() => {
  const nav = document.querySelector(".output-filter");
  const requested = new URLSearchParams(location.search).get("type") || "";
  const links = [...nav.querySelectorAll("a")];
  const active = links.find(link => link.dataset.type === requested) || links[0];
  active.setAttribute("aria-current", "page");
  nav.hidden = false;

  const type = active.dataset.type;
  if (!type) return;

  for (const entry of document.querySelectorAll("[data-kind]")) {
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
