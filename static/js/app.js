(function () {
    "use strict";

    var body = document.body;
    var menuButton = document.querySelector("[data-nav-toggle]");
    var overlay = document.querySelector("[data-nav-overlay]");

    function closeMenu() {
        body.classList.remove("nav-open");
        if (menuButton) menuButton.setAttribute("aria-expanded", "false");
    }

    if (menuButton) {
        menuButton.addEventListener("click", function () {
            var open = body.classList.toggle("nav-open");
            menuButton.setAttribute("aria-expanded", String(open));
        });
    }
    if (overlay) overlay.addEventListener("click", closeMenu);
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") closeMenu();
    });

    document.querySelectorAll("[data-choice-filter]").forEach(function (input) {
        var target = document.getElementById(input.getAttribute("data-choice-filter"));
        if (!target) return;
        input.addEventListener("input", function () {
            var query = input.value.trim().toLocaleLowerCase("es");
            target.querySelectorAll("label").forEach(function (label) {
                label.closest("div").hidden = !label.textContent.toLocaleLowerCase("es").includes(query);
            });
        });
    });

    var categoryList = document.getElementById("category-choices");
    var zoneList = document.getElementById("zone-choices");
    function updateSelections() {
        var categories = categoryList ? categoryList.querySelectorAll("input:checked").length : 0;
        var zones = zoneList ? zoneList.querySelectorAll("input:checked").length : 0;
        document.querySelectorAll("[data-category-count]").forEach(function (node) { node.textContent = String(categories); });
        document.querySelectorAll("[data-zone-count]").forEach(function (node) { node.textContent = String(zones); });
        document.querySelectorAll("[data-query-count]").forEach(function (node) { node.textContent = String(categories * zones); });
    }
    [categoryList, zoneList].forEach(function (list) {
        if (list) list.addEventListener("change", updateSelections);
    });
    updateSelections();
}());
