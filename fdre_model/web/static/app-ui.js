(function () {
  "use strict";

  function onReady(callback) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback);
      return;
    }
    callback();
  }

  function storageKey(section) {
    var trigger = section.querySelector(".collapsible-trigger");
    return trigger && trigger.id ? "fdre:collapsible:" + location.pathname + ":" + trigger.id : "";
  }

  function setExpanded(section, expanded, persist) {
    var trigger = section.querySelector(".collapsible-trigger");
    var panel = section.querySelector(".collapsible-panel");
    if (!trigger || !panel) {
      return;
    }
    trigger.setAttribute("aria-expanded", expanded ? "true" : "false");
    panel.hidden = !expanded;
    section.classList.toggle("is-open", expanded);
    section.dispatchEvent(new CustomEvent("fdre:collapsible-toggle", {
      bubbles: true,
      detail: { expanded: expanded }
    }));
    if (persist) {
      try {
        window.localStorage.setItem(storageKey(section), expanded ? "true" : "false");
      } catch (_error) {
        // localStorage is an enhancement only.
      }
    }
  }

  function initialExpanded(section) {
    if (section.dataset.forceOpen === "true") {
      return true;
    }
    try {
      var saved = window.localStorage.getItem(storageKey(section));
      if (saved === "true" || saved === "false") {
        return saved === "true";
      }
    } catch (_error) {
      // Ignore storage failures.
    }
    return section.dataset.defaultOpen === "true";
  }

  function enhanceCollapsibles() {
    Array.prototype.slice.call(document.querySelectorAll("[data-collapsible]")).forEach(function (section) {
      var trigger = section.querySelector(".collapsible-trigger");
      if (!trigger) {
        return;
      }
      setExpanded(section, initialExpanded(section), false);
      trigger.addEventListener("click", function () {
        var expanded = trigger.getAttribute("aria-expanded") === "true";
        setExpanded(section, !expanded, true);
      });
    });
  }

  function parseLocalDateTime(value) {
    if (!value) {
      return null;
    }
    var parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function localDateKey(date) {
    return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
  }

  function splitDateTime(value, defaultTime) {
    var text = value || "";
    var parts = text.split("T");
    return {
      date: parts[0] || "",
      time: (parts[1] || defaultTime || "").slice(0, 5)
    };
  }

  function composeDateTime(date, time) {
    return date && time ? date + "T" + time : "";
  }

  function compareDateKeys(first, second) {
    if (!first || !second) {
      return 0;
    }
    return first === second ? 0 : first < second ? -1 : 1;
  }

  function monthTitle(date) {
    return date.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  }

  function durationLabel(start, end) {
    var minutes = Math.round((end.getTime() - start.getTime()) / 60000);
    if (minutes < 0) {
      return "Invalid";
    }
    if (minutes < 60) {
      return minutes + "m";
    }
    var hours = Math.ceil(minutes / 60);
    if (hours < 24) {
      return hours + "h";
    }
    var days = Math.ceil(hours / 24);
    return days + "d";
  }

  function enhanceDateRangeEditors() {
    Array.prototype.slice.call(document.querySelectorAll(".date-custom-form")).forEach(function (form) {
      var panel = form.closest("[data-date-range-panel]");
      var menuScreen = panel && panel.querySelector("[data-date-menu-screen]");
      var directForm = panel && panel.querySelector("[data-date-direct-form]");
      var directStart = directForm && directForm.querySelector("[data-direct-start]");
      var directEnd = directForm && directForm.querySelector("[data-direct-end]");
      var directDuration = directForm && directForm.querySelector("[data-direct-duration]");
      var directSubmit = directForm && directForm.querySelector("button[type='submit']");
      var startInput = form.querySelector("[data-range-start]");
      var endInput = form.querySelector("[data-range-end]");
      var startDateInput = form.querySelector("[data-range-start-date]");
      var startTimeInput = form.querySelector("[data-range-start-time]");
      var endDateInput = form.querySelector("[data-range-end-date]");
      var endTimeInput = form.querySelector("[data-range-end-time]");
      var duration = form.querySelector("[data-range-duration]");
      var feedback = form.querySelector("[data-range-feedback]");
      var submit = form.querySelector("button[type='submit']");
      var calendarGrid = form.querySelector("[data-calendar-grid]");
      var calendarTitle = form.querySelector("[data-calendar-title]");
      var prevMonth = form.querySelector("[data-calendar-prev]");
      var nextMonth = form.querySelector("[data-calendar-next]");
      var calendarOpen = panel && panel.querySelector("[data-calendar-screen-open]");
      var calendarBack = form.querySelector("[data-calendar-screen-back]");
      var selectionPhase = "start";
      var initialStart = splitDateTime(startInput && startInput.value, "00:00");
      var initialEnd = splitDateTime(endInput && endInput.value, "23:59");
      var viewDate = initialStart.date ? new Date(initialStart.date + "T00:00") : new Date();

      if (!startInput || !endInput || !duration || !startDateInput || !startTimeInput || !endDateInput || !endTimeInput) {
        return;
      }

      startDateInput.value = initialStart.date;
      startTimeInput.value = initialStart.time || "00:00";
      endDateInput.value = initialEnd.date;
      endTimeInput.value = initialEnd.time || "23:59";

      function setCalendarScreen(open) {
        if (!panel) {
          return;
        }
        if (open) {
          syncCalendarFromDirect();
          updateRangeState();
          renderCalendar();
        }
        panel.classList.toggle("is-calendar-screen", open);
        if (menuScreen) {
          menuScreen.hidden = open;
        }
        form.hidden = !open;
        if (calendarOpen) {
          calendarOpen.setAttribute("aria-expanded", open ? "true" : "false");
        }
      }

      function syncHiddenFields() {
        startInput.value = composeDateTime(startDateInput.value, startTimeInput.value || "00:00");
        endInput.value = composeDateTime(endDateInput.value, endTimeInput.value || "23:59");
      }

      function syncDirectFromCalendar() {
        if (!directStart || !directEnd) {
          return;
        }
        directStart.value = startInput.value;
        directEnd.value = endInput.value;
        updateDirectRangeState();
      }

      function syncCalendarFromDirect() {
        if (!directStart || !directEnd || !directStart.value || !directEnd.value) {
          return;
        }
        var directStartParts = splitDateTime(directStart.value, "00:00");
        var directEndParts = splitDateTime(directEnd.value, "23:59");
        startDateInput.value = directStartParts.date;
        startTimeInput.value = directStartParts.time || "00:00";
        endDateInput.value = directEndParts.date;
        endTimeInput.value = directEndParts.time || "23:59";
        if (directStartParts.date) {
          viewDate = new Date(directStartParts.date + "T00:00");
        }
        syncHiddenFields();
      }

      function updateDirectRangeState() {
        if (!directForm || !directStart || !directEnd || !directDuration) {
          return;
        }
        var start = parseLocalDateTime(directStart.value);
        var end = parseLocalDateTime(directEnd.value);
        var hasBoth = Boolean(start && end);
        var invalid = hasBoth && end <= start;
        directForm.classList.toggle("range-invalid", invalid);
        directDuration.textContent = hasBoth ? durationLabel(start, end) : "Range";
        if (directSubmit) {
          directSubmit.disabled = invalid || !hasBoth;
        }
      }

      function selectCalendarDate(dateKey) {
        if (!startDateInput.value || selectionPhase === "start") {
          startDateInput.value = dateKey;
          endDateInput.value = dateKey;
          startTimeInput.value = "00:00";
          endTimeInput.value = "23:59";
          selectionPhase = "end";
        } else {
          if (compareDateKeys(dateKey, startDateInput.value) < 0) {
            endDateInput.value = startDateInput.value;
            startDateInput.value = dateKey;
          } else {
            endDateInput.value = dateKey;
          }
          selectionPhase = "start";
        }
        syncHiddenFields();
        updateRangeState();
        renderCalendar();
      }

      function renderCalendar() {
        if (!calendarGrid || !calendarTitle) {
          return;
        }
        var month = new Date(viewDate.getFullYear(), viewDate.getMonth(), 1);
        var firstWeekday = month.getDay();
        var daysInMonth = new Date(viewDate.getFullYear(), viewDate.getMonth() + 1, 0).getDate();
        var todayKey = localDateKey(new Date());
        var startKey = startDateInput.value;
        var endKey = endDateInput.value;
        calendarTitle.textContent = monthTitle(month);
        calendarGrid.innerHTML = "";
        for (var blank = 0; blank < firstWeekday; blank += 1) {
          calendarGrid.appendChild(document.createElement("span"));
        }
        for (var day = 1; day <= daysInMonth; day += 1) {
          var date = new Date(viewDate.getFullYear(), viewDate.getMonth(), day);
          var key = localDateKey(date);
          var button = document.createElement("button");
          button.type = "button";
          button.className = "date-calendar-day";
          button.textContent = String(day);
          button.dataset.date = key;
          button.setAttribute("aria-label", key);
          if (key === todayKey) {
            button.classList.add("is-today");
          }
          if (key === startKey) {
            button.classList.add("is-start");
          }
          if (key === endKey) {
            button.classList.add("is-end");
          }
          if (startKey && endKey && compareDateKeys(key, startKey) >= 0 && compareDateKeys(key, endKey) <= 0) {
            button.classList.add("in-range");
          }
          button.addEventListener("click", function (event) {
            selectCalendarDate(event.currentTarget.dataset.date);
          });
          calendarGrid.appendChild(button);
        }
      }

      function updateRangeState() {
        syncHiddenFields();
        var start = parseLocalDateTime(startInput.value);
        var end = parseLocalDateTime(endInput.value);
        var hasBoth = Boolean(start && end);
        var invalid = hasBoth && end <= start;
        form.classList.toggle("range-invalid", invalid);
        duration.textContent = hasBoth ? durationLabel(start, end) : "Range";
        if (feedback) {
          feedback.textContent = invalid ? "End must be after start." : hasBoth ? "Range ready." : "Choose both start and end.";
        }
        if (submit) {
          submit.disabled = invalid || !hasBoth;
        }
        syncDirectFromCalendar();
      }

      [startDateInput, startTimeInput, endDateInput, endTimeInput].forEach(function (input) {
        input.addEventListener("input", function () {
          selectionPhase = "start";
          if (startDateInput.value) {
            viewDate = new Date(startDateInput.value + "T00:00");
          }
          updateRangeState();
          renderCalendar();
        });
      });
      [directStart, directEnd].forEach(function (input) {
        if (!input) {
          return;
        }
        input.addEventListener("input", updateDirectRangeState);
      });
      if (prevMonth) {
        prevMonth.addEventListener("click", function () {
          viewDate = new Date(viewDate.getFullYear(), viewDate.getMonth() - 1, 1);
          renderCalendar();
        });
      }
      if (nextMonth) {
        nextMonth.addEventListener("click", function () {
          viewDate = new Date(viewDate.getFullYear(), viewDate.getMonth() + 1, 1);
          renderCalendar();
        });
      }
      if (calendarOpen) {
        calendarOpen.setAttribute("aria-expanded", "false");
        calendarOpen.addEventListener("click", function () {
          setCalendarScreen(true);
        });
      }
      if (calendarBack) {
        calendarBack.addEventListener("click", function () {
          syncDirectFromCalendar();
          setCalendarScreen(false);
        });
      }
      updateRangeState();
      updateDirectRangeState();
      renderCalendar();
      setCalendarScreen(false);
    });
  }

  function detailCardFor(details) {
    return details.querySelector(".rule-detail-card, .history-business-card, .row-detail-grid");
  }

  function clearFloatingDetail(details) {
    var card = detailCardFor(details);
    if (!card) {
      return;
    }
    ["position", "top", "left", "right", "width", "maxHeight"].forEach(function (name) {
      card.style.removeProperty(name);
    });
  }

  function positionFloatingDetail(details) {
    if (!details.open || !details.closest(".syncfusion-grid-host")) {
      clearFloatingDetail(details);
      return;
    }
    var summary = details.querySelector("summary");
    var card = detailCardFor(details);
    if (!summary || !card) {
      return;
    }
    var gap = 8;
    var padding = 16;
    var summaryRect = summary.getBoundingClientRect();
    var desiredWidth = Math.min(430, window.innerWidth - padding * 2);
    var maxHeight = Math.min(560, window.innerHeight - padding * 2);
    var left = Math.min(summaryRect.right - desiredWidth, window.innerWidth - desiredWidth - padding);
    left = Math.max(padding, left);

    card.style.position = "fixed";
    card.style.right = "auto";
    card.style.width = desiredWidth + "px";
    card.style.maxHeight = maxHeight + "px";
    card.style.left = left + "px";
    card.style.top = "0px";

    var measuredHeight = Math.min(card.scrollHeight, maxHeight);
    var top = summaryRect.bottom + gap;
    if (top + measuredHeight > window.innerHeight - padding) {
      top = summaryRect.top - measuredHeight - gap;
    }
    card.style.top = Math.max(padding, top) + "px";
  }

  var floatingDetailsBound = false;

  function enhanceFloatingDetails() {
    if (floatingDetailsBound) {
      return;
    }
    floatingDetailsBound = true;

    document.addEventListener("toggle", function (event) {
      var details = event.target;
      if (!(details instanceof HTMLDetailsElement) || !details.matches(".syncfusion-grid-host .row-detail")) {
        return;
      }
      if (details.open) {
        var host = details.closest(".syncfusion-grid-host");
        if (host) {
          Array.prototype.slice.call(host.querySelectorAll(".row-detail[open]")).forEach(function (other) {
            if (other !== details) {
              other.open = false;
              clearFloatingDetail(other);
            }
          });
        }
        positionFloatingDetail(details);
        return;
      }
      clearFloatingDetail(details);
    }, true);

    ["resize", "scroll"].forEach(function (eventName) {
      window.addEventListener(eventName, function () {
        Array.prototype.slice.call(document.querySelectorAll(".syncfusion-grid-host .row-detail[open]")).forEach(positionFloatingDetail);
      }, { passive: true });
    });
  }

  function datasetInt(element, name, fallback) {
    var parsed = parseInt(element.dataset[name] || "", 10);
    return Number.isNaN(parsed) ? fallback : parsed;
  }

  function formatRefreshSeconds(seconds) {
    if (seconds >= 3600 && seconds % 3600 === 0) {
      return (seconds / 3600) + "h";
    }
    if (seconds >= 60 && seconds % 60 === 0) {
      return (seconds / 60) + "m";
    }
    return seconds + "s";
  }

  function enhanceLiveAutoRefresh() {
    Array.prototype.slice.call(document.querySelectorAll("[data-live-auto-refresh]")).forEach(function (control) {
      if (control.dataset.autoRefreshReady === "true") {
        return;
      }
      control.dataset.autoRefreshReady = "true";

      var toggle = control.querySelector("[data-auto-refresh-toggle]");
      var secondsInput = control.querySelector("[data-auto-refresh-seconds]");
      var status = control.querySelector("[data-auto-refresh-status]");
      if (!toggle || !secondsInput) {
        return;
      }

      var minSeconds = Math.max(5, datasetInt(control, "minSeconds", 15));
      var maxSeconds = Math.max(minSeconds, datasetInt(control, "maxSeconds", 3600));
      var defaultSeconds = Math.min(maxSeconds, Math.max(minSeconds, datasetInt(control, "defaultSeconds", 60)));
      var enabledKey = "fdre:live:auto-refresh:enabled";
      var secondsKey = "fdre:live:auto-refresh:seconds";
      var timer = 0;

      function clampSeconds(value, fallback) {
        var parsed = parseInt(value, 10);
        if (Number.isNaN(parsed)) {
          return fallback;
        }
        return Math.min(maxSeconds, Math.max(minSeconds, parsed));
      }

      function currentSeconds(normalize) {
        if (!String(secondsInput.value || "").trim()) {
          return null;
        }
        var seconds = clampSeconds(secondsInput.value, defaultSeconds);
        if (normalize) {
          secondsInput.value = String(seconds);
        }
        return seconds;
      }

      function setStatus(text) {
        if (status) {
          status.textContent = text;
        }
      }

      function persist() {
        try {
          window.localStorage.setItem(enabledKey, toggle.checked ? "true" : "false");
          var seconds = currentSeconds(false);
          if (seconds !== null) {
            window.localStorage.setItem(secondsKey, String(seconds));
          }
        } catch (_error) {
          // Auto-refresh preferences are local convenience state only.
        }
      }

      function schedule() {
        window.clearTimeout(timer);
        control.classList.toggle("is-enabled", toggle.checked);
        if (!toggle.checked) {
          var savedSeconds = currentSeconds(false) || defaultSeconds;
          setStatus("Off · " + formatRefreshSeconds(savedSeconds));
          return;
        }
        var seconds = currentSeconds(false);
        if (seconds === null) {
          setStatus("Set frequency");
          return;
        }
        setStatus("On · " + formatRefreshSeconds(seconds));
        timer = window.setTimeout(function () {
          if (document.hidden) {
            schedule();
            return;
          }
          window.location.reload();
        }, seconds * 1000);
      }

      try {
        var savedSeconds = window.localStorage.getItem(secondsKey);
        var savedEnabled = window.localStorage.getItem(enabledKey);
        secondsInput.value = String(savedSeconds ? clampSeconds(savedSeconds, defaultSeconds) : defaultSeconds);
        toggle.checked = savedEnabled === "true";
      } catch (_error) {
        secondsInput.value = String(defaultSeconds);
      }

      secondsInput.min = String(minSeconds);
      secondsInput.max = String(maxSeconds);
      secondsInput.title = "Allowed range: " + minSeconds + "-" + maxSeconds + " seconds";

      toggle.addEventListener("change", function () {
        persist();
        schedule();
      });
      secondsInput.addEventListener("input", function () {
        persist();
        schedule();
      });
      ["change", "blur"].forEach(function (eventName) {
        secondsInput.addEventListener(eventName, function () {
          currentSeconds(true);
          persist();
          schedule();
        });
      });
      document.addEventListener("visibilitychange", function () {
        if (!document.hidden) {
          schedule();
        }
      });
      schedule();
    });
  }

  onReady(function () {
    enhanceCollapsibles();
    enhanceDateRangeEditors();
    enhanceFloatingDetails();
    enhanceLiveAutoRefresh();
  });
}());
