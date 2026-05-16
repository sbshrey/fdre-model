(function () {
  "use strict";

  var GRID_SELECTOR = "table[data-syncfusion-grid]";

  function onReady(callback) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback);
      return;
    }
    callback();
  }

  function textOf(element) {
    return (element.textContent || "").replace(/\s+/g, " ").trim();
  }

  function cleanHeader(header, index) {
    var label = textOf(header);
    return label || "Column " + (index + 1);
  }

  function parsePageSize(table, rowCount) {
    var raw = Number(table.dataset.gridPageSize || 25);
    if (!Number.isFinite(raw) || raw <= 0) {
      return Math.min(Math.max(rowCount, 1), 25);
    }
    return raw;
  }

  function buildGridModel(table) {
    var headers = Array.prototype.slice.call(table.querySelectorAll("thead th"));
    var rows = Array.prototype.slice.call(table.querySelectorAll("tbody tr"));
    var columns = headers.map(function (header, index) {
      var field = "c" + index;
      var htmlField = field + "Html";
      return {
        field: field,
        headerText: cleanHeader(header, index),
        template: function (data) {
          return data[htmlField] || "";
        },
        disableHtmlEncode: false,
        minWidth: index === 0 ? 150 : 110,
        width: header.dataset.gridWidth ? Number(header.dataset.gridWidth) : undefined,
        clipMode: "EllipsisWithTooltip"
      };
    });
    var data = rows.map(function (row, rowIndex) {
      var item = {
        __rowClass: row.className || "",
        __rowIndex: rowIndex + 1
      };
      headers.forEach(function (_header, index) {
        var cell = row.children[index];
        var field = "c" + index;
        item[field] = cell ? textOf(cell) : "";
        item[field + "Html"] = cell ? cell.innerHTML.trim() : "";
      });
      return item;
    });
    return { columns: columns, data: data };
  }

  function registerLicense() {
    if (!window.FDRE_SYNCFUSION_LICENSE_KEY || !window.ej || !window.ej.base || !window.ej.base.registerLicense) {
      return;
    }
    window.ej.base.registerLicense(window.FDRE_SYNCFUSION_LICENSE_KEY);
  }

  function injectGridModules() {
    if (!window.ej || !window.ej.grids || !window.ej.grids.Grid || !window.ej.grids.Grid.Inject) {
      return;
    }
    var modules = [
      window.ej.grids.Page,
      window.ej.grids.Sort,
      window.ej.grids.Filter,
      window.ej.grids.Search,
      window.ej.grids.Resize,
      window.ej.grids.Reorder,
      window.ej.grids.Toolbar
    ].filter(Boolean);
    if (modules.length) {
      window.ej.grids.Grid.Inject.apply(window.ej.grids.Grid, modules);
    }
  }

  function enhanceTable(table) {
    if (table.dataset.syncfusionReady === "1") {
      return;
    }
    if (!window.ej || !window.ej.grids || !window.ej.grids.Grid) {
      return;
    }

    var model = buildGridModel(table);
    var pageSize = parsePageSize(table, model.data.length);
    var host = document.createElement("div");
    host.className = "syncfusion-grid-host";
    host.dataset.gridSource = table.dataset.syncfusionGrid || "";
    table.parentNode.insertBefore(host, table);

    try {
      var grid = new window.ej.grids.Grid({
        dataSource: model.data,
        columns: model.columns,
        allowPaging: model.data.length > pageSize,
        pageSettings: {
          pageSize: pageSize,
          pageSizes: [10, 25, 50, 100]
        },
        allowSorting: true,
        allowFiltering: true,
        allowResizing: true,
        allowReordering: true,
        enableHover: true,
        enableHtmlSanitizer: false,
        gridLines: "Horizontal",
        height: table.dataset.gridHeight || "auto",
        toolbar: ["Search"],
        filterSettings: { type: "Menu" },
        rowDataBound: function (args) {
          if (args.data && args.data.__rowClass) {
            String(args.data.__rowClass).split(/\s+/).filter(Boolean).forEach(function (className) {
              args.row.classList.add(className);
            });
          }
        }
      });
      grid.appendTo(host);
      table.dataset.syncfusionReady = "1";
      table.classList.add("syncfusion-source-table");
      table.setAttribute("aria-hidden", "true");
    } catch (error) {
      host.remove();
      console.warn("Syncfusion Grid enhancement failed", error);
    }
  }

  function enhanceTables() {
    registerLicense();
    injectGridModules();
    Array.prototype.slice.call(document.querySelectorAll(GRID_SELECTOR)).forEach(enhanceTable);
  }

  onReady(enhanceTables);
}());
