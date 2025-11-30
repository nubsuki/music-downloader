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
      // Failed items are objects with a url and error property
      if (typeof item === "object" && item.url) {
        const li = createListItem(item.url, itemClass);
        const errorDetail = document.createElement("small");
        errorDetail.textContent = item.error.replace('failed: ', '');
        li.appendChild(errorDetail);
        listElement.appendChild(li);
      } else {
        const li = createListItem(item, itemClass);
        listElement.appendChild(li);
      }
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
      const files = await response.json();
      updateList(downloadedList, files, "item-downloaded");
    } catch (error) {
      console.error("Error updating downloaded files:", error);
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

  // Initial and periodic status updates
  updateStatus(); // Initial status check
  setInterval(updateStatus, 3000); // Poll every 3 seconds
});
