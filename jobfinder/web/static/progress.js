// Follows background work on a page: fills the bar, then reloads the page when the work has finished.
(function () {
  var box = document.getElementById("progress-box");
  if (!box) return;
  var text = document.getElementById("progress-text");
  var fill = document.getElementById("progress-fill");
  function check() {
    fetch(box.dataset.statusUrl).then(function (reply) { return reply.json(); }).then(function (work) {
      if (work.status !== "running") { location.reload(); return; }
      if (work.total) {
        text.textContent = "Checked " + work.done + " of " + work.total + " companies" + (work.current ? " (just read " + work.current + ")" : "");
        fill.style.width = (100 * work.done / work.total) + "%";
      }
      setTimeout(check, 1000);
    }).catch(function () { setTimeout(check, 3000); });
  }
  check();
})();
