document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("url-form");
  const urlInput = document.getElementById("url-input");
  const messageContainer = document.getElementById("message-container");

  const activityLog = document.getElementById("activity-log");
  const downloadedList = document.getElementById("downloaded-list");
  const playlistTrackedList = document.getElementById("playlist-tracked-list");
  const playlistTrackForm = document.getElementById("playlist-track-form");
  const playlistTrackInput = document.getElementById("playlist-track-input");
  const playlistRefreshBtn = document.getElementById("playlist-refresh-btn");
  const audioPlayer = document.getElementById("audio-player");
  const nowPlaying = document.getElementById("now-playing");
  const cfgEl = document.getElementById("app-config");
  window.APP_CONFIG = {
    enableDelete: !!(cfgEl && cfgEl.dataset.enableDelete === "true"),
    autoPlaylist: !!(cfgEl && cfgEl.dataset.autoPlaylist === "true"),
  };

  // Modal elements
  const modal = document.getElementById("playlist-modal");
  const modalInput = document.getElementById("playlist-name-input");
  const modalOkBtn = document.getElementById("modal-ok-btn");
  const modalCancelBtn = document.getElementById("modal-cancel-btn");
  const submitBtn = form.querySelector('button[type="submit"]');
  const createM3uCheckbox = document.getElementById("create-m3u-checkbox");
  const tabButtons = document.querySelectorAll(".tab-button");
  const tabPanels = document.querySelectorAll(".tab-panel");

  const setActiveTab = (targetId) => {
    tabButtons.forEach((button) => {
      const isActive = button.dataset.tabTarget === targetId;
      button.classList.toggle("active", isActive);
      button.setAttribute("aria-selected", isActive ? "true" : "false");
      button.setAttribute("tabindex", isActive ? "0" : "-1");
    });

    tabPanels.forEach((panel) => {
      const isActive = panel.id === targetId;
      panel.classList.toggle("active", isActive);
      panel.hidden = !isActive;
    });
  };

  tabButtons.forEach((button) => {
    button.addEventListener("click", () => {
      setActiveTab(button.dataset.tabTarget);
    });
  });

  if (tabButtons.length > 0) {
    const activeButton = document.querySelector(".tab-button.active");
    setActiveTab(
      activeButton ? activeButton.dataset.tabTarget : tabButtons[0].dataset.tabTarget
    );
  }

  let pendingUrl = "";
  let pendingTrackUrl = "";
  let modalAction = "";

  const isPlaylistUrl = (rawUrl) => {
    try {
      const parsed = new URL(rawUrl);
      const host = (parsed.hostname || "").toLowerCase();

      const isSpotify = host === "spotify.com" || host.endsWith(".spotify.com") || parsed.protocol === "spotify:";
      if (isSpotify) {
        const p = parsed.pathname || "";
        return p.includes("/playlist/") || p.includes("/album/") || rawUrl.includes(":playlist:") || rawUrl.includes(":album:");
      }

      const isYouTube = host === "youtube.com" || host.endsWith(".youtube.com") || host === "youtu.be";
      if (isYouTube) {
        if ((parsed.pathname || "").startsWith("/playlist")) return true;
        return parsed.searchParams.has("list");
      }

      return false;
    } catch {
      return false;
    }
  };

  /**
   * Helper function to create a list item for a URL.
   * @param {string} url - The URL to display.
   * @param {string} itemClass - The CSS class for styling the item.
   * @returns {HTMLLIElement}
   */
  const createListItem = (url, itemClass = "") => {
    const li = document.createElement("li");
    li.textContent = url;
    if (itemClass) {
      li.classList.add(itemClass);
    }
    return li;
  };

  /**
   * Helper function to update a list with new items.
   * @param {HTMLElement} listElement - The <ul> element.
   * @param {Array} items - The array of items to display.
   * @param {string} itemClass - The CSS class to apply to each item.
   */
  const updateList = (listElement, items, itemClass) => {
    listElement.innerHTML = ""; // Clear existing items
    if (items.length === 0) {
      listElement.innerHTML = "<li>None</li>";
      return;
    }
    items.forEach((item) => {
      let li;
      li = createListItem(item, itemClass);
      if (listElement.id === "downloaded-list") {
        const textSpan = document.createElement("span");
        textSpan.textContent = item;

        const playButton = document.createElement("button");
        playButton.textContent = "Play";
        playButton.className = "play-button";
        playButton.dataset.filename = item;

        const buttonContainer = document.createElement("div");
        buttonContainer.className = "list-item-buttons";
        buttonContainer.appendChild(playButton);

        if (window.APP_CONFIG && window.APP_CONFIG.enableDelete) {
          const deleteButton = document.createElement("button");
          deleteButton.textContent = "Delete";
          deleteButton.className = "delete-button";
          deleteButton.dataset.filename = item;
          buttonContainer.appendChild(deleteButton);
        }

        li.innerHTML = "";
        li.appendChild(textSpan);
        li.appendChild(buttonContainer);
      }
      listElement.appendChild(li);
    });
  };

  let lastLogId = 0;
  const POLL_INTERVAL_LOGS_MS = 1500;
  const POLL_INTERVAL_FILES_MS = 5000;
  let isFetchingLogs = false;
  let isUpdatingDownloaded = false;
  let isUpdatingTracked = false;
  const networkErrorLogAt = new Map();

  const shouldLogNetworkError = (key, cooldownMs = 10000) => {
    const now = Date.now();
    const lastAt = networkErrorLogAt.get(key) || 0;
    if (now - lastAt < cooldownMs) {
      return false;
    }
    networkErrorLogAt.set(key, now);
    return true;
  };

  const isLikelyNetworkError = (error) => {
    const message = error && error.message ? String(error.message) : "";
    return error && (error.name === "AbortError" || error instanceof TypeError || message.includes("NetworkError") || message.includes("Failed to fetch"));
  };

  const fetchJsonWithRetry = async (url, options = {}, retries = 1, timeoutMs = 12000) => {
    let lastError;
    for (let attempt = 0; attempt <= retries; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await fetch(url, { ...options, signal: controller.signal, cache: "no-store" });
        clearTimeout(timer);
        if (!response.ok) {
          throw new Error(`Request failed with status ${response.status}`);
        }
        return await response.json();
      } catch (error) {
        clearTimeout(timer);
        lastError = error;
        if (!isLikelyNetworkError(error) || attempt === retries) {
          throw error;
        }
        await new Promise((resolve) => setTimeout(resolve, 300 * (attempt + 1)));
      }
    }
    throw lastError;
  };

  const appendLogEntry = (entry) => {
    if (!activityLog) return;
    const line = entry && typeof entry.message === "string" ? entry.message : "";
    if (activityLog.textContent) {
      activityLog.textContent += `\n${line}`;
    } else {
      activityLog.textContent = line;
    }
    activityLog.scrollTop = activityLog.scrollHeight;
  };

  const fetchLogs = async () => {
    if (isFetchingLogs) return;
    isFetchingLogs = true;
    try {
      const data = await fetchJsonWithRetry(`/api/logs?after=${lastLogId}`);
      if (Array.isArray(data.entries)) {
        data.entries.forEach(appendLogEntry);
      }
      if (typeof data.latest_id === "number") {
        lastLogId = data.latest_id;
      }
    } catch (error) {
      if (shouldLogNetworkError("logs") || !isLikelyNetworkError(error)) {
        console.error("Error fetching logs:", error);
      }
    } finally {
      isFetchingLogs = false;
    }
  };

  // Function to fetch and update the list of downloaded files
  const updateDownloadedFiles = async () => {
    if (isUpdatingDownloaded) return;
    isUpdatingDownloaded = true;
    try {
      const data = await fetchJsonWithRetry("/api/downloaded_files");
      updateList(downloadedList, data.files, "item-downloaded");
      if (typeof applyDownloadedFilter === "function") {
        applyDownloadedFilter();
      }
      updateMp3Counter(data.mp3_count);
    } catch (error) {
      if (shouldLogNetworkError("files") || !isLikelyNetworkError(error)) {
        console.error("Error updating downloaded files:", error);
      }
    } finally {
      isUpdatingDownloaded = false;
    }
  };

  // Function to update MP3 counter display
  const updateMp3Counter = (count) => {
    let counterElement = document.getElementById("mp3-counter");
    if (!counterElement) {
      const downloadedSection = document.getElementById("downloaded-section");
      counterElement = document.createElement("div");
      counterElement.id = "mp3-counter";
      counterElement.className = "mp3-counter";
      downloadedSection.insertBefore(counterElement, downloadedSection.firstChild.nextSibling);
    }
    counterElement.textContent = `${count} MP3 file${count !== 1 ? "s" : ""} downloaded`;
  };

  const renderTrackedPlaylists = (items) => {
    if (!playlistTrackedList) return;
    playlistTrackedList.innerHTML = "";
    if (!Array.isArray(items) || items.length === 0) {
      playlistTrackedList.innerHTML = "<li>None</li>";
      return;
    }

    items.forEach((item) => {
      const li = document.createElement("li");
      li.className = "playlist-track-item";

      const details = document.createElement("div");
      details.className = "playlist-track-details";

      const titleRow = document.createElement("div");
      titleRow.style.display = "flex";
      titleRow.style.alignItems = "center";
      titleRow.style.gap = "8px";

      const title = document.createElement("div");
      title.className = "playlist-track-title";
      title.textContent = item.name || "Playlist";

      const autoRefreshLabel = document.createElement("label");
      autoRefreshLabel.style.display = "flex";
      autoRefreshLabel.style.alignItems = "center";
      autoRefreshLabel.style.gap = "4px";
      autoRefreshLabel.style.fontSize = "0.85em";
      autoRefreshLabel.style.cursor = "pointer";

      const autoRefreshCheckbox = document.createElement("input");
      autoRefreshCheckbox.type = "checkbox";
      autoRefreshCheckbox.checked = !!item.auto_refresh;
      autoRefreshCheckbox.dataset.playlistId = item.id;
      autoRefreshCheckbox.className = "auto-refresh-checkbox";

      const autoRefreshText = document.createElement("span");
      autoRefreshText.textContent = "Auto-refresh daily";

      autoRefreshLabel.appendChild(autoRefreshCheckbox);
      autoRefreshLabel.appendChild(autoRefreshText);

      titleRow.appendChild(title);
      titleRow.appendChild(autoRefreshLabel);

      const meta = document.createElement("div");
      meta.className = "playlist-track-meta";
      const tracked = Number(item.tracked_track_count || 0);
      const remote = Number(item.remote_track_count || tracked);
      const added = Number(item.new_tracks || 0);
      const removed = Number(item.removed_tracks || 0);
      let diffText = "No changes detected";
      if (added > 0 || removed > 0) {
        const parts = [];
        if (added > 0) parts.push(`+${added}`);
        if (removed > 0) parts.push(`-${removed}`);
        diffText = `Changes: ${parts.join("/")}`;
      }
      meta.textContent = `${tracked} tracked song${tracked !== 1 ? "s" : ""} • ${remote} currently in playlist • ${diffText}`;

      details.appendChild(titleRow);
      details.appendChild(meta);
      li.appendChild(details);

      const actions = document.createElement("div");
      actions.className = "playlist-item-actions";

      const newTracks = Number(item.new_tracks || 0);
      const removedTracks = Number(item.removed_tracks || 0);
      const hasChanges = newTracks > 0 || removedTracks > 0;
      if (hasChanges) {
        const updateBtn = document.createElement("button");
        updateBtn.className = "playlist-update-button";
        updateBtn.dataset.playlistId = item.id;
        const parts = [];
        if (newTracks > 0) parts.push(`+${newTracks}`);
        if (removedTracks > 0) parts.push(`-${removedTracks}`);
        updateBtn.textContent = `Update (${parts.join("/")})`;
        actions.appendChild(updateBtn);
      }

      if (window.APP_CONFIG && window.APP_CONFIG.enableDelete) {
        const deleteBtn = document.createElement("button");
        deleteBtn.className = "playlist-delete-button";
        deleteBtn.dataset.playlistId = item.id;
        deleteBtn.title = "Remove playlist tracking";
        deleteBtn.setAttribute("aria-label", "Remove playlist tracking");
        deleteBtn.innerHTML = "<i class=\"bi bi-trash3\"></i>";
        actions.appendChild(deleteBtn);
      }

      li.appendChild(actions);
      playlistTrackedList.appendChild(li);
    });
  };

  const updateTrackedPlaylists = async (options = {}) => {
    if (!playlistTrackedList || isUpdatingTracked) return false;
    isUpdatingTracked = true;
    const { notify = false } = options;
    try {
      const data = await fetchJsonWithRetry("/api/tracked_playlists", {}, 1, 20000);
      renderTrackedPlaylists(data.playlists || []);
      if (typeof applyPlaylistFilter === "function") {
        applyPlaylistFilter();
      }
      if (notify) {
        displayMessage("Playlist updates checked.");
      }
      return true;
    } catch (error) {
      if (shouldLogNetworkError("tracked") || !isLikelyNetworkError(error)) {
        console.error("Error loading tracked playlists:", error);
      }
      if (notify) {
        displayMessage("Failed to check playlist updates.", "error");
      }
      return false;
    } finally {
      isUpdatingTracked = false;
    }
  };

  // Function to display transient messages
  const displayMessage = (message, type = "info") => {
    messageContainer.textContent = message;
    messageContainer.style.color = type === "error" ? "red" : "green";
    setTimeout(() => {
      messageContainer.textContent = "";
    }, 4000);
  };

  // Event Listener for form submission
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const url = urlInput.value.trim();

    if (!url) {
      displayMessage("Please enter a URL.", "error");
      return;
    }

    submitBtn.disabled = true;
    const originalBtnText = submitBtn.textContent;
    submitBtn.textContent = "Fetching info...";

    try {
      const manualCreateM3u = !!(createM3uCheckbox && createM3uCheckbox.checked);
      const autoPlaylistEnabled = !!(window.APP_CONFIG && window.APP_CONFIG.autoPlaylist);
      const shouldCreatePlaylist = manualCreateM3u || (autoPlaylistEnabled && isPlaylistUrl(url));

      if (!shouldCreatePlaylist) {
        const response = await fetch("/api/download", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url, create_m3u: false }),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data && data.error ? data.error : "Failed to queue download.");
        }
        displayMessage(data.message || "URL added to queue.");
        urlInput.value = "";
        fetchLogs();
        return;
      }

      const infoResponse = await fetch(`/api/get-info?url=${encodeURIComponent(url)}`);
      if (!infoResponse.ok) throw new Error("Failed to fetch URL info");

      const info = await infoResponse.json();
      modalAction = "download-playlist";
      pendingUrl = url;
      modalInput.value = info.title || "Playlist";
      // Show track playlist checkbox, hide auto-refresh checkbox when opening modal for download-playlist
      const trackPlaylistLabel = document.getElementById("track-playlist-modal-label");
      const trackPlaylistCheckbox = document.getElementById("track-playlist-modal-checkbox");
      const autoRefreshLabel = document.getElementById("auto-refresh-modal-label");
      if (trackPlaylistLabel) trackPlaylistLabel.style.display = "flex";
      if (trackPlaylistCheckbox) trackPlaylistCheckbox.checked = false;
      if (autoRefreshLabel) autoRefreshLabel.style.display = "none";
      modal.classList.add("show");
      modalInput.focus();
      modalInput.select();
    } catch (error) {
      console.error("Error submitting URL:", error);
      displayMessage(error.message || "Request failed. Please try again.", "error");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = originalBtnText;
    }
  });

  if (playlistTrackForm) {
    playlistTrackForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const url = (playlistTrackInput.value || "").trim();
      if (!url) {
        displayMessage("Please enter a playlist URL.", "error");
        return;
      }

      let suggestedName = "Playlist";
      try {
        const infoResponse = await fetch(`/api/get-info?url=${encodeURIComponent(url)}`);
        if (infoResponse.ok) {
          const info = await infoResponse.json();
          suggestedName = info.title || suggestedName;
        }
      } catch (error) {
        console.error("Error fetching playlist info for tracking:", error);
      }

      modalAction = "track-playlist";
      pendingTrackUrl = url;
      modalInput.value = suggestedName;
      // Hide track playlist checkbox, reset auto-refresh checkbox when opening modal for track-playlist
      const trackPlaylistLabel = document.getElementById("track-playlist-modal-label");
      const autoRefreshCheckbox = document.getElementById("auto-refresh-modal-checkbox");
      const autoRefreshLabel = document.getElementById("auto-refresh-modal-label");
      if (trackPlaylistLabel) trackPlaylistLabel.style.display = "none";
      if (autoRefreshCheckbox) autoRefreshCheckbox.checked = false;
      if (autoRefreshLabel) autoRefreshLabel.style.display = "flex";
      modal.classList.add("show");
      modalInput.focus();
      modalInput.select();
    });
  }

  if (playlistRefreshBtn) {
    playlistRefreshBtn.addEventListener("click", async () => {
      playlistRefreshBtn.disabled = true;
      const originalText = playlistRefreshBtn.textContent;
      playlistRefreshBtn.textContent = "Refreshing All...";
      try {
        await updateTrackedPlaylists({ notify: true });
      } finally {
        playlistRefreshBtn.disabled = false;
        playlistRefreshBtn.textContent = originalText;
      }
    });
  }

  if (playlistTrackedList) {
    playlistTrackedList.addEventListener("click", async (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }

      const playlistId = target.dataset.playlistId;
      if (!playlistId) return;

      if (target.classList.contains("playlist-update-button")) {
        try {
          const response = await fetch("/api/tracked_playlists/ack-update", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: playlistId }),
          });
          const data = await response.json();
          if (!response.ok) {
            throw new Error(data && data.error ? data.error : "Failed to update playlist tracking.");
          }
          displayMessage(data.message || "Playlist resync started.");
          updateTrackedPlaylists();
        } catch (error) {
          console.error("Error acknowledging playlist update:", error);
          displayMessage(error.message || "Failed to update playlist tracking.", "error");
        }
        return;
      }

      if (target.classList.contains("playlist-delete-button")) {
        const confirmRemove = confirm("Remove this playlist from tracking?");
        if (!confirmRemove) {
          return;
        }

        const removeM3u8 = confirm("Also remove the playlist .m3u8 file from disk?\n\nPress OK to remove .m3u8, or Cancel to keep it.");

        try {
          const response = await fetch("/api/tracked_playlists/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: playlistId, delete_m3u8: removeM3u8 }),
          });
          const data = await response.json();
          if (!response.ok) {
            throw new Error(data && data.error ? data.error : "Failed to remove tracked playlist.");
          }
          displayMessage(data.message || "Playlist removed from tracking.");
          updateTrackedPlaylists();
        } catch (error) {
          console.error("Error deleting tracked playlist:", error);
          displayMessage(error.message || "Failed to remove tracked playlist.", "error");
        }
      }
    });

    // Handle auto-refresh checkbox changes
    playlistTrackedList.addEventListener("change", async (event) => {
      const target = event.target;
      if (!(target instanceof HTMLInputElement) || !target.classList.contains("auto-refresh-checkbox")) {
        return;
      }

      const playlistId = target.dataset.playlistId;
      const isChecked = target.checked;

      try {
        const response = await fetch("/api/tracked_playlists", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: playlistId, auto_refresh: isChecked }),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data && data.error ? data.error : "Failed to update auto-refresh setting.");
        }
        displayMessage(isChecked ? "Auto-refresh enabled for this playlist." : "Auto-refresh disabled for this playlist.");
      } catch (error) {
        console.error("Error updating auto-refresh:", error);
        displayMessage(error.message || "Failed to update auto-refresh setting.", "error");
        // Revert checkbox state on error
        target.checked = !isChecked;
      }
    });
  }

  // Modal handlers
  modalCancelBtn.addEventListener("click", () => {
    modal.classList.remove("show");
    pendingUrl = "";
    pendingTrackUrl = "";
    modalAction = "";
  });

  modalOkBtn.addEventListener("click", async () => {
    const playlistName = modalInput.value.trim();
    if (!playlistName) {
      alert("Please enter a playlist name.");
      return;
    }

    modal.classList.remove("show");

    if (modalAction === "track-playlist") {
      try {
        const autoRefreshCheckbox = document.getElementById("auto-refresh-modal-checkbox");
        const autoRefresh = autoRefreshCheckbox ? autoRefreshCheckbox.checked : false;
        const response = await fetch("/api/tracked_playlists", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: pendingTrackUrl, name: playlistName, auto_refresh: autoRefresh }),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data && data.error ? data.error : "Failed to track playlist.");
        }
        playlistTrackInput.value = "";
        displayMessage(data.message || (data.exists ? "Playlist is already tracked." : "Playlist added to tracking."));
        updateTrackedPlaylists();
      } catch (error) {
        console.error("Error tracking playlist:", error);
        displayMessage(error.message || "Failed to track playlist.", "error");
      } finally {
        pendingTrackUrl = "";
        modalAction = "";
      }
      return;
    }

    submitBtn.disabled = true;
    const originalBtnText = submitBtn.textContent;
    submitBtn.textContent = "Creating...";

    try {
      const makeRequest = async (payload) => {
        const resp = await fetch("/api/create-playlist", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await resp.json();
        return { resp, data };
      };

      const trackPlaylistCheckbox = document.getElementById("track-playlist-modal-checkbox");
      const trackPlaylist = trackPlaylistCheckbox ? trackPlaylistCheckbox.checked : false;

      let payload = { url: pendingUrl, name: playlistName, overwrite: false, track_playlist: trackPlaylist };
      let { resp, data } = await makeRequest(payload);

      if (resp.status === 409 && data && data.exists) {
        const suggested = data.suggested_name || `${playlistName} (1)`;
        const confirmOverwrite = confirm(`Playlist "${playlistName}" already exists.\n\nPress OK to overwrite the existing file, or Cancel to create a new one named "${suggested}".`);
        if (confirmOverwrite) {
          payload.overwrite = true;
          ({ resp, data } = await makeRequest(payload));
        } else {
          payload.name = suggested;
          payload.overwrite = false;
          ({ resp, data } = await makeRequest(payload));
        }
      }

      if (!resp.ok) {
        throw new Error(data && data.error ? data.error : "Failed to create playlist.");
      }

      displayMessage(data.message || `Playlist '${data.playlist_id}' created! ${data.track_count} tracks queued.`);
      urlInput.value = "";
      fetchLogs();
      updateDownloadedFiles();
      if (trackPlaylist) {
        updateTrackedPlaylists();
      }
    } catch (error) {
      console.error("Error creating playlist:", error);
      displayMessage(error.message, "error");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = originalBtnText;
      pendingUrl = "";
      modalAction = "";
    }
  });

  // Allow Enter key in modal input
  modalInput.addEventListener("keyup", (event) => {
    if (event.key === "Enter") {
      modalOkBtn.click();
    }
  });

  // Event listener for the downloaded files search bar
  const downloadedSearchInput = document.getElementById(
    "downloaded-search-input"
  );
  const playlistSearchInput = document.getElementById(
    "playlist-search-input"
  );
  const normalizeForSearch = (str) => {
    return (str || "")
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "");
  };
  const applyDownloadedFilter = () => {
    const filter = normalizeForSearch(downloadedSearchInput.value);
    const items = downloadedList.getElementsByTagName("li");
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      const text = item.textContent || item.innerText;
      const normalizedText = normalizeForSearch(text);
      item.style.display = normalizedText.indexOf(filter) > -1 ? "" : "none";
    }
  };
  downloadedSearchInput.addEventListener("keyup", applyDownloadedFilter);

  // Event listener for the playlist search bar
  const applyPlaylistFilter = () => {
    const filter = normalizeForSearch(playlistSearchInput.value);
    const items = playlistTrackedList.getElementsByTagName("li");
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      const text = item.textContent || item.innerText;
      const normalizedText = normalizeForSearch(text);
      item.style.display = normalizedText.indexOf(filter) > -1 ? "" : "none";
    }
  };
  if (playlistSearchInput) {
    playlistSearchInput.addEventListener("keyup", applyPlaylistFilter);
  }

  // Event listener for deleting downloaded files
  downloadedList.addEventListener("click", async (event) => {
    if (event.target.classList.contains("play-button")) {
      const filename = event.target.dataset.filename;
      if (!filename) return;

      const audioSrc = `/downloads/${encodeURI(filename)}`;
      audioPlayer.src = audioSrc;
      audioPlayer.load();
      audioPlayer.play();
      nowPlaying.textContent = `Now Playing: ${filename}`;
    }
    
    if (event.target.classList.contains("delete-button")) {
      if (!(window.APP_CONFIG && window.APP_CONFIG.enableDelete)) {
        return;
      }

      const filename = event.target.dataset.filename;
      if (!filename) return;

      if (!confirm(`Are you sure you want to delete ${filename}?`)) {
        return;
      }

      try {
        const response = await fetch("/api/delete_file", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ filename: filename }),
        });

        const data = await response.json();

        if (data.success) {
          displayMessage("File deleted successfully!");
          await updateDownloadedFiles(); // Refresh the list
        } else {
          throw new Error(data.error || "Failed to delete file.");
        }
      } catch (error) {
        console.error("Error deleting file:", error);
        displayMessage(error.message, "error");
      }
    }
  });

  // Initial and periodic status updates
  fetchLogs();
  updateDownloadedFiles();
  updateTrackedPlaylists();
  setInterval(fetchLogs, POLL_INTERVAL_LOGS_MS);
  setInterval(updateDownloadedFiles, POLL_INTERVAL_FILES_MS);

  window.addEventListener("online", () => {
    fetchLogs();
    updateDownloadedFiles();
    updateTrackedPlaylists();
  });
});
