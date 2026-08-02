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

    var floatingHelp = document.createElement("div");
    floatingHelp.id = "floating-help";
    floatingHelp.className = "floating-help";
    floatingHelp.setAttribute("role", "tooltip");
    floatingHelp.hidden = true;
    document.body.appendChild(floatingHelp);

    var activeHelpTarget = null;

    function showHelp(target) {
        var explanation = target.getAttribute("data-tooltip");
        if (!explanation) return;
        activeHelpTarget = target;
        floatingHelp.textContent = explanation;
        floatingHelp.hidden = false;
        var targetBox = target.getBoundingClientRect();
        var helpBox = floatingHelp.getBoundingClientRect();
        var top = targetBox.top - helpBox.height - 8;
        if (top < 8) top = targetBox.bottom + 8;
        var left = targetBox.left + (targetBox.width - helpBox.width) / 2;
        left = Math.max(8, Math.min(left, window.innerWidth - helpBox.width - 8));
        floatingHelp.style.top = String(top) + "px";
        floatingHelp.style.left = String(left) + "px";
    }

    function hideHelp(target) {
        if (activeHelpTarget !== target) return;
        activeHelpTarget = null;
        floatingHelp.hidden = true;
    }

    document.querySelectorAll("[data-tooltip]").forEach(function (target) {
        var naturallyFocusable = target.matches("a, button, input, select, textarea, [tabindex]");
        if (!naturallyFocusable) target.setAttribute("tabindex", "0");
        if (!target.hasAttribute("aria-label") && !naturallyFocusable) {
            target.setAttribute(
                "aria-label",
                target.textContent.trim() + ". " + target.getAttribute("data-tooltip")
            );
        }
        target.setAttribute("aria-describedby", floatingHelp.id);
        target.addEventListener("mouseenter", function () { showHelp(target); });
        target.addEventListener("mouseleave", function () { hideHelp(target); });
        target.addEventListener("focus", function () { showHelp(target); });
        target.addEventListener("blur", function () { hideHelp(target); });
    });

    window.addEventListener("scroll", function () {
        if (activeHelpTarget) showHelp(activeHelpTarget);
    }, true);
    window.addEventListener("resize", function () {
        if (activeHelpTarget) showHelp(activeHelpTarget);
    });

    document.querySelectorAll("[data-choice-filter]").forEach(function (input) {
        var target = document.getElementById(input.getAttribute("data-choice-filter"));
        if (!target) return;
        input.addEventListener("input", function () {
            var query = input.value.trim().toLocaleLowerCase("es");
            target.querySelectorAll("[data-choice-item]").forEach(function (item) {
                item.hidden = !item.textContent.toLocaleLowerCase("es").includes(query);
            });
        });
    });

    var categoryList = document.getElementById("category-choices");
    var zoneList = document.getElementById("zone-choices");
    var zoneMaps = document.querySelectorAll("[data-zone-map]");

    function zoneInput(zoneId) {
        if (!zoneList) return null;
        var matchingInput = null;
        zoneList.querySelectorAll("input").forEach(function (input) {
            if (input.value === zoneId) matchingInput = input;
        });
        return matchingInput;
    }

    function syncZoneMap(root) {
        var scope = root || document;
        scope.querySelectorAll("[data-zone-id]").forEach(function (shape) {
            var input = zoneInput(shape.getAttribute("data-zone-id"));
            shape.setAttribute("aria-checked", String(Boolean(input && input.checked)));
        });
    }

    function updateSelections() {
        var categories = categoryList ? categoryList.querySelectorAll("input:checked").length : 0;
        var zones = zoneList ? zoneList.querySelectorAll("input:checked").length : 0;
        document.querySelectorAll("[data-category-count]").forEach(function (node) { node.textContent = String(categories); });
        document.querySelectorAll("[data-zone-count]").forEach(function (node) { node.textContent = String(zones); });
        document.querySelectorAll("[data-query-count]").forEach(function (node) { node.textContent = String(categories * zones); });
        syncZoneMap();
    }

    [categoryList, zoneList].forEach(function (list) {
        if (list) list.addEventListener("change", updateSelections);
    });

    function setChoiceGroup(targetId, checked) {
        var target = document.getElementById(targetId);
        if (!target) return;
        target.querySelectorAll("input[type=checkbox]").forEach(function (input) {
            input.checked = checked;
        });
        updateSelections();
    }

    document.querySelectorAll("[data-choice-select-all]").forEach(function (button) {
        button.addEventListener("click", function () {
            setChoiceGroup(button.getAttribute("data-choice-select-all"), true);
        });
    });
    document.querySelectorAll("[data-choice-clear]").forEach(function (button) {
        button.addEventListener("click", function () {
            setChoiceGroup(button.getAttribute("data-choice-clear"), false);
        });
    });

    function provincePanel(provinceId) {
        return document.querySelector('[data-province-panel="' + provinceId + '"]');
    }

    document.querySelectorAll("[data-province-toggle]").forEach(function (input) {
        var panel = provincePanel(input.getAttribute("data-province-toggle"));
        if (!panel) return;
        function syncProvince() {
            panel.hidden = !input.checked;
            if (!input.checked) {
                panel.querySelectorAll('input[type="checkbox"]').forEach(function (district) {
                    district.checked = false;
                });
            } else {
                var map = panel.querySelector("[data-zone-map]");
                if (map) loadZoneMap(map);
            }
            updateSelections();
        }
        input.addEventListener("change", syncProvince);
    });

    document.querySelectorAll("[data-district-filter]").forEach(function (input) {
        var target = document.getElementById(input.getAttribute("data-district-filter"));
        if (!target) return;
        input.addEventListener("input", function () {
            var query = input.value.trim().toLocaleLowerCase("es");
            target.querySelectorAll("[data-choice-item]").forEach(function (item) {
                item.hidden = !item.textContent.toLocaleLowerCase("es").includes(query);
            });
        });
    });

    function setDistrictGroup(targetId, checked) {
        var target = document.getElementById(targetId);
        if (!target) return;
        target.querySelectorAll('input[type="checkbox"]').forEach(function (input) {
            input.checked = checked;
        });
        updateSelections();
    }

    document.querySelectorAll("[data-district-select-all]").forEach(function (button) {
        button.addEventListener("click", function () {
            setDistrictGroup(button.getAttribute("data-district-select-all"), true);
        });
    });
    document.querySelectorAll("[data-district-clear]").forEach(function (button) {
        button.addEventListener("click", function () {
            setDistrictGroup(button.getAttribute("data-district-clear"), false);
        });
    });

    function geometryPath(geometry, project) {
        var polygons = [];
        if (geometry && geometry.type === "Polygon") polygons = [geometry.coordinates];
        if (geometry && geometry.type === "MultiPolygon") polygons = geometry.coordinates;
        var commands = [];
        polygons.forEach(function (rings) {
            rings.forEach(function (ring) {
                if (!Array.isArray(ring) || ring.length < 3) return;
                ring.forEach(function (coordinate, index) {
                    if (!Array.isArray(coordinate) || coordinate.length < 2) return;
                    var point = project(Number(coordinate[0]), Number(coordinate[1]));
                    if (!point) return;
                    commands.push((index === 0 ? "M" : "L") + point[0].toFixed(2) + " " + point[1].toFixed(2));
                });
                commands.push("Z");
            });
        });
        return commands.join(" ");
    }

    function renderZoneShape(canvas, zone) {
        var namespace = "http://www.w3.org/2000/svg";
        var pathData = zone.path;
        if (!pathData && zone.geometry && typeof zone.project === "function") {
            pathData = geometryPath(zone.geometry, zone.project);
        }
        if (!pathData || !zoneInput(String(zone.id))) return false;
        var shape = document.createElementNS(namespace, "path");
        var title = document.createElementNS(namespace, "title");
        title.textContent = zone.name;
        shape.appendChild(title);
        shape.setAttribute("d", pathData);
        shape.setAttribute("class", "zone-map__shape");
        shape.setAttribute("data-zone-id", String(zone.id));
        shape.setAttribute("role", "checkbox");
        shape.setAttribute("tabindex", "0");
        shape.setAttribute("aria-label", "Zona " + zone.name);
        shape.addEventListener("click", function () {
            var input = zoneInput(String(zone.id));
            if (!input) return;
            input.checked = !input.checked;
            input.dispatchEvent(new Event("change", { bubbles: true }));
        });
        shape.addEventListener("keydown", function (event) {
            if (event.key !== "Enter" && event.key !== " ") return;
            event.preventDefault();
            shape.dispatchEvent(new Event("click"));
        });
        canvas.appendChild(shape);
        return true;
    }

    function clearSvg(svg) {
        while (svg.firstChild) svg.removeChild(svg.firstChild);
    }

    function renderZoneMap(map, payload) {
        var canvas = map.querySelector("[data-zone-map-canvas]");
        var emptyMessage = map.querySelector("[data-zone-map-empty]");
        var count = map.querySelector("[data-zone-map-count]");
        var zones = payload && Array.isArray(payload.zones) ? payload.zones : [];
        var rendered = 0;
        if (!canvas) return;
        clearSvg(canvas);
        canvas.setAttribute("viewBox", payload.viewBox || "0 0 1000 560");
        zones.forEach(function (zone) {
            if (renderZoneShape(canvas, zone)) rendered += 1;
        });
        map.setAttribute("data-zone-map-loaded", "true");
        map.removeAttribute("data-zone-map-loading");
        if (count) count.textContent = String(rendered);

        if (!rendered) {
            canvas.hidden = true;
            if (emptyMessage) emptyMessage.hidden = false;
        } else {
            canvas.hidden = false;
            if (emptyMessage) emptyMessage.hidden = true;
        }
        syncZoneMap(map);
    }

    function failZoneMap(map) {
        var canvas = map.querySelector("[data-zone-map-canvas]");
        var emptyMessage = map.querySelector("[data-zone-map-empty]");
        map.removeAttribute("data-zone-map-loading");
        if (canvas) canvas.hidden = true;
        if (emptyMessage) emptyMessage.hidden = false;
    }

    function loadZoneMap(map) {
        if (map.getAttribute("data-zone-map-loaded") === "true") return;
        if (map.getAttribute("data-zone-map-loading") === "true") return;
        var url = map.getAttribute("data-zone-map-url");
        if (!url || !window.fetch) {
            failZoneMap(map);
            return;
        }
        map.setAttribute("data-zone-map-loading", "true");
        window.fetch(url, {
            credentials: "same-origin",
            headers: { "Accept": "application/json" }
        }).then(function (response) {
            if (!response.ok) throw new Error("zone map unavailable");
            return response.json();
        }).then(function (payload) {
            renderZoneMap(map, payload);
        }).catch(function () {
            failZoneMap(map);
        });
    }

    function initializeZoneMaps() {
        zoneMaps.forEach(function (map) {
            if (!map.closest("[hidden]")) loadZoneMap(map);
        });
    }

    initializeZoneMaps();
    updateSelections();
}());
