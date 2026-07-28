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
    var zoneMapCanvas = document.querySelector("[data-zone-map-canvas]");

    function zoneInput(zoneId) {
        if (!zoneList) return null;
        var matchingInput = null;
        zoneList.querySelectorAll("input").forEach(function (input) {
            if (input.value === zoneId) matchingInput = input;
        });
        return matchingInput;
    }

    function syncZoneMap() {
        if (!zoneMapCanvas) return;
        zoneMapCanvas.querySelectorAll("[data-zone-id]").forEach(function (shape) {
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

    function initializeZoneMap() {
        if (!zoneMapCanvas) return;
        var dataNode = document.getElementById("campaign-zone-map-data");
        if (!dataNode) return;
        var zones;
        try {
            zones = JSON.parse(dataNode.textContent);
        } catch (error) {
            zones = [];
        }
        var validZones = zones.filter(function (zone) {
            return Array.isArray(zone.bbox) && zone.bbox.length === 4 && zone.bbox.every(Number.isFinite);
        });
        var emptyMessage = document.querySelector("[data-zone-map-empty]");
        if (!validZones.length) {
            zoneMapCanvas.hidden = true;
            if (emptyMessage) emptyMessage.hidden = false;
            return;
        }

        var minX = Math.min.apply(null, validZones.map(function (zone) { return zone.bbox[0]; }));
        var minY = Math.min.apply(null, validZones.map(function (zone) { return zone.bbox[1]; }));
        var maxX = Math.max.apply(null, validZones.map(function (zone) { return zone.bbox[2]; }));
        var maxY = Math.max.apply(null, validZones.map(function (zone) { return zone.bbox[3]; }));
        var width = 1000;
        var height = 560;
        var padding = 24;
        var xRange = Math.max(maxX - minX, 0.000001);
        var yRange = Math.max(maxY - minY, 0.000001);
        var scale = Math.min((width - padding * 2) / xRange, (height - padding * 2) / yRange);
        var offsetX = (width - xRange * scale) / 2;
        var offsetY = (height - yRange * scale) / 2;
        var project = function (longitude, latitude) {
            if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return null;
            return [offsetX + (longitude - minX) * scale, offsetY + (maxY - latitude) * scale];
        };
        var namespace = "http://www.w3.org/2000/svg";
        var rendered = 0;

        validZones.forEach(function (zone) {
            var pathData = geometryPath(zone.geometry, project);
            if (!pathData || !zoneInput(String(zone.id))) return;
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
            zoneMapCanvas.appendChild(shape);
            rendered += 1;
        });

        if (!rendered) {
            zoneMapCanvas.hidden = true;
            if (emptyMessage) emptyMessage.hidden = false;
        }
    }

    initializeZoneMap();
    updateSelections();
}());
