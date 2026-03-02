document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("url-form");
  const urlInput = document.getElementById("url-input");
  const messageContainer = document.getElementById("message-container");

  const activityLog = document.getElementById("activity-log");
  const downloadedList = document.getElementById("downloaded-list");
  const audioPlayer = document.getElementById("audio-player");
  const nowPlaying = document.getElementById("now-playing");
  const cfgEl = document.getElementById("app-config");
  window.APP_CONFIG = { enableDelete: !!(cfgEl && cfgEl.dataset.enableDelete === "true") };

  // Modal elements
  const modal = document.getElementById("playlist-modal");
  const modalInput = document.getElementById("playlist-name-input");
  const modalOkBtn = document.getElementById("modal-ok-btn");
  const modalCancelBtn = document.getElementById("modal-cancel-btn");
  const submitBtn = form.querySelector('button[type="submit"]');

  let pendingUrl = "";

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
    try {
      const response = await fetch(`/api/logs?after=${lastLogId}`);
      if (!response.ok) {
        throw new Error("Failed to fetch logs.");
      }
      const data = await response.json();
      if (Array.isArray(data.entries)) {
        data.entries.forEach(appendLogEntry);
      }
      if (typeof data.latest_id === "number") {
        lastLogId = data.latest_id;
      }
    } catch (error) {
      console.error("Error fetching logs:", error);
    }
  };

  // Function to fetch and update the list of downloaded files
  const updateDownloadedFiles = async () => {
    try {
      const response = await fetch("/api/downloaded_files");
      if (!response.ok) {
        throw new Error("Failed to fetch downloaded files.");
      }
      const data = await response.json();
      
      // Update the downloaded files list
      updateList(downloadedList, data.files, "item-downloaded");

      // Re-apply current search filter after list refresh
      if (typeof applyDownloadedFilter === "function") {
        applyDownloadedFilter();
      }
      
      // Update MP3 counter
      updateMp3Counter(data.mp3_count);
    } catch (error) {
      console.error("Error updating downloaded files:", error);
    }
  };

  // Function to update MP3 counter display
  const updateMp3Counter = (count) => {
    let counterElement = document.getElementById("mp3-counter");
    
    // Create counter element if it doesn't exist
    if (!counterElement) {
      const downloadedSection = document.getElementById("downloaded-section");
      counterElement = document.createElement("div");
      counterElement.id = "mp3-counter";
      counterElement.className = "mp3-counter";
      downloadedSection.insertBefore(counterElement, downloadedSection.firstChild.nextSibling);
    }
    
    counterElement.textContent = `${count} MP3 file${count !== 1 ? 's' : ''} downloaded`;
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

    // Set loading state
    submitBtn.disabled = true;
    const originalBtnText = submitBtn.textContent;
    submitBtn.textContent = "Fetching info...";

    try {
      const infoResponse = await fetch(`/api/get-info?url=${encodeURIComponent(url)}`);
      if (!infoResponse.ok) throw new Error("Failed to fetch URL info");
      
      const info = await infoResponse.json();
      pendingUrl = url;
      modalInput.value = info.title || "Playlist";
      modal.classList.add("show");
      modalInput.focus();
      modalInput.select();
    } catch (error) {
      console.error("Error fetching info:", error);
      displayMessage("Error fetching URL info. Please try again.", "error");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = originalBtnText;
    }
  });

  // Modal handlers
  modalCancelBtn.addEventListener("click", () => {
    modal.classList.remove("show");
    pendingUrl = "";
  });

  modalOkBtn.addEventListener("click", async () => {
    const playlistName = modalInput.value.trim();
    if (!playlistName) {
      alert("Please enter a playlist name.");
      return;
    }

    modal.classList.remove("show");
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

      let payload = { url: pendingUrl, name: playlistName, overwrite: false };
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
    } catch (error) {
      console.error("Error creating playlist:", error);
      displayMessage(error.message, "error");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = originalBtnText;
      pendingUrl = "";
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
  setInterval(fetchLogs, 1000);
  setInterval(updateDownloadedFiles, 3000);
});
