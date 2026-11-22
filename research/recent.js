// Use UTC dates so every reader sees the same three-calendar-month window.
const today = new Date().toISOString().slice(0, 10);
for (const badge of document.querySelectorAll("[data-first-published]")) {
  const published = badge.dataset.firstPublished;
  const [year, month, day] = published.split("-").map(Number);
  const lastDay = new Date(Date.UTC(year, month + 3, 0)).getUTCDate();
  const expires = new Date(Date.UTC(year, month + 2, Math.min(day, lastDay)));
  badge.hidden = today < published || today >= expires.toISOString().slice(0, 10);
}
