document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("url-form");
  const urlInput = document.getElementById("url-input");
  const messageContainer = document.getElementById("message-container");

  // Get references to the new status lists
  const downloadingList = document.getElementById("downloading-list");
  const queuedList = document.getElementById("queued-list");
  const completedList = document.getElementById("completed-list");
  const failedList = document.getElementById("failed-list");
  const downloadedList = document.getElementById("downloaded-list");
  const audioPlayer = document.getElementById("audio-player");
  const nowPlaying = document.getElementById("now-playing");

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
      // Handle failed items as objects
      if (typeof item === "object" && item.url) {
        li = createListItem(item.url, itemClass);
        const errorDetail = document.createElement("small");
        errorDetail.textContent = item.error.replace("failed: ", "");
        li.appendChild(errorDetail);
      } else {
        li = createListItem(item, itemClass);
        // add a delete button
        if (listElement.id === "downloaded-list") {
          const textSpan = document.createElement("span");
          textSpan.textContent = item;

          const deleteButton = document.createElement("button");
          deleteButton.textContent = "Delete";
          deleteButton.className = "delete-button";
          deleteButton.dataset.filename = item;

          const playButton = document.createElement("button");
          playButton.textContent = "Play";
          playButton.className = "play-button";
          playButton.dataset.filename = item;

          const buttonContainer = document.createElement("div");
          buttonContainer.className = "list-item-buttons";
          buttonContainer.appendChild(playButton);
          buttonContainer.appendChild(deleteButton);

          li.innerHTML = "";
          li.appendChild(textSpan);
          li.appendChild(buttonContainer);
        }
      }
      listElement.appendChild(li);
    });
  };

  // Function to fetch and update queue status
  const updateStatus = async () => {
    try {
      const response = await fetch("/api/status");
      if (!response.ok) {
        throw new Error("Failed to fetch status.");
      }
      const data = await response.json();

      // Update each list
      updateList(downloadingList, data.downloading, "item-downloading");
      updateList(queuedList, data.queued, "item-queued");
      updateList(completedList, data.completed, "item-completed");
      updateList(failedList, data.failed, "item-failed");

      // Also update the list of downloaded files
      await updateDownloadedFiles();
    } catch (error) {
      console.error("Error updating status:", error);
      displayMessage("Error updating queue status.", "error");
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

    try {
      const response = await fetch("/api/add_url", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ url: url }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || "Failed to add URL.");
      }

      displayMessage("URL successfully added to the queue!");
      urlInput.value = ""; // Clear the input field
      updateStatus(); // Update status immediately
    } catch (error) {
      console.error("Error submitting URL:", error);
      displayMessage(error.message, "error");
    }
  });

  // Event listener for the downloaded files search bar
  const downloadedSearchInput = document.getElementById(
    "downloaded-search-input"
  );
  downloadedSearchInput.addEventListener("keyup", () => {
    const filter = downloadedSearchInput.value.toLowerCase();
    const items = downloadedList.getElementsByTagName("li");
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      const text = item.textContent || item.innerText;
      if (text.toLowerCase().indexOf(filter) > -1) {
        item.style.display = "";
      } else {
        item.style.display = "none";
      }
    }
  });

  // Event listener for deleting downloaded files
  downloadedList.addEventListener("click", async (event) => {
    if (event.target.classList.contains("play-button")) {
      const filename = event.target.dataset.filename;
      if (!filename) return;

      const audioSrc = `/downloads/${encodeURIComponent(filename)}`;
      audioPlayer.src = audioSrc;
      audioPlayer.load();
      audioPlayer.play();
      nowPlaying.textContent = `Now Playing: ${filename}`;
    }
    
    if (event.target.classList.contains("delete-button")) {
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
  updateStatus(); // Initial status check
  setInterval(updateStatus, 3000); // Poll every 3 seconds
});
