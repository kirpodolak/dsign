(function() {
  // Global App objects
  window.App = window.App || {};
  window.App.Helpers = window.App.Helpers || {};
  window.App.Alerts = window.App.Alerts || {
    show: function(message, type = 'info') {
      const alertBox = document.createElement('div');
      alertBox.className = `alert alert-${type}`;
      alertBox.textContent = message;
      document.body.appendChild(alertBox);
      setTimeout(() => alertBox.remove(), 3000);
    }
  };

  // DOM Elements
  const mediaGallery = document.getElementById('media-gallery');
  const uploadForm = document.getElementById('upload-form');
  const fileInput = document.getElementById('file-upload');
  const uploadBtn = document.getElementById('upload-btn');
  const deleteBtn = document.getElementById('delete-selected');
  const selectAllBtn = document.getElementById('select-all');
  const searchInput = document.getElementById('search-input');
  const sortSelect = document.getElementById('sort-select');
  const groupSelect = document.getElementById('group-select');
  const previewModal = document.getElementById('preview-modal');
  const closeModal = document.querySelector('.close-modal');
  const previewContainer = document.getElementById('preview-container');
  const previewFilename = document.getElementById('preview-filename');
  const previewFilesize = document.getElementById('preview-filesize');
  const previewDate = document.getElementById('preview-date');

  // State
  let currentFiles = [];
  let selectedFiles = new Set();

  // Constants
  const ALLOWED_IMAGE_TYPES = ['jpg', 'jpeg', 'png', 'gif'];
  const ALLOWED_VIDEO_TYPES = ['mp4', 'webm', 'ogg'];
  const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50MB
  const PLACEHOLDER_IMAGE = '/static/images/placeholder.jpg';

  // Utility functions
  function getFileExtension(filename) {
    if (!filename) return '';
    const parts = filename.split('.');
    return parts.length > 1 ? parts.pop().toLowerCase() : '';
  }

  function isValidFile(file) {
    if (!file) return false;
    const ext = getFileExtension(file.name);
    return (ALLOWED_IMAGE_TYPES.includes(ext) || 
            ALLOWED_VIDEO_TYPES.includes(ext)) && 
           file.size <= MAX_FILE_SIZE;
  }

  function formatFileSize(bytes) {
    if (typeof bytes !== 'number' || bytes < 0) return '0 Bytes';
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  function formatDate(timestamp) {
    if (!timestamp) return 'Unknown date';
    try {
      return new Date(timestamp).toLocaleString();
    } catch {
      return 'Invalid date';
    }
  }

  // Load media files from server
  async function loadMediaFiles() {
    try {
      const response = await fetch('/api/media/files');
      
      if (!response.ok) {
        throw new Error(`Server returned ${response.status} status`);
      }
      
      const data = await response.json();
      
      if (data?.success && Array.isArray(data.files)) {
        currentFiles = data.files.map(file => ({
          name: file.filename || 'unnamed',
          type: getFileExtension(file.filename),
          date: file.uploadDate || file.modified || Date.now(),
          size: file.size || 0,
          path: `/api/media/${file.filename}`, // Fixed path
          mimetype: file.mimetype || null,
          dimensions: file.dimensions || null
        }));
        renderGallery(currentFiles);
      } else {
        throw new Error(data?.error || 'Invalid response format');
      }
    } catch (error) {
      console.error('Failed to load media files:', error);
      if (window.App?.Alerts?.show) {
        window.App.Alerts.show(`Error loading files: ${error.message}`, 'error');
      }
    }
  }

  // Filter and sort files
  function processFiles(files) {
    if (!Array.isArray(files)) return [];
    
    // Filter by search
    let filtered = [...files];
    if (searchInput?.value) {
      const searchTerm = searchInput.value.toLowerCase().trim();
      filtered = filtered.filter(file => 
        file.name?.toLowerCase().includes(searchTerm)
      );
    }

    // Sort
    const sortValue = sortSelect?.value;
    filtered.sort((a, b) => {
      switch (sortValue) {
        case 'name-asc': return a.name?.localeCompare(b.name);
        case 'name-desc': return b.name?.localeCompare(a.name);
        case 'date-newest': return (b.date || 0) - (a.date || 0);
        case 'date-oldest': return (a.date || 0) - (b.date || 0);
        case 'type': return a.type?.localeCompare(b.type);
        default: return 0;
      }
    });

    return filtered;
  }

  // Group files
  function groupFiles(files) {
    if (!Array.isArray(files)) return [];
    const groupValue = groupSelect?.value;
    if (groupValue === 'none') return files;

    const groups = {};
    files.forEach(file => {
      let key = 'Other';
      if (groupValue === 'type') {
        key = file.type?.toUpperCase() || 'OTHER';
      } else if (groupValue === 'date') {
        try {
          const date = new Date(file.date || Date.now());
          key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
        } catch {
          key = 'Unknown date';
        }
      }

      if (!groups[key]) groups[key] = [];
      groups[key].push(file);
    });

    return groups;
  }

  // Render media gallery
  function renderGallery(files) {
    if (!mediaGallery) return;
    
    mediaGallery.innerHTML = '';
    
    if (!Array.isArray(files) || files.length === 0) {
      mediaGallery.innerHTML = '<p class="empty-message">No media files found.</p>';
      toggleDeleteButton(false);
      toggleSelectAllButton(false);
      return;
    }

    const processedFiles = processFiles(files);
    const groupedFiles = groupFiles(processedFiles);

    if (Array.isArray(groupedFiles)) {
      renderFiles(groupedFiles);
    } else {
      // Render grouped files
      Object.entries(groupedFiles).forEach(([groupName, groupFiles]) => {
        const groupHeader = document.createElement('div');
        groupHeader.className = 'group-header';
        groupHeader.textContent = groupName;
        mediaGallery.appendChild(groupHeader);
        renderFiles(groupFiles);
      });
    }

    toggleDeleteButton(selectedFiles.size > 0);
    toggleSelectAllButton(processedFiles.length > 0);
  }

  function renderFiles(files) {
    if (!Array.isArray(files)) return;

    files.forEach(file => {
      if (!file) return;
      
      const item = document.createElement('div');
      item.classList.add('file-item');
      item.dataset.filename = file.name;

      // Checkbox
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.className = 'file-checkbox';
      checkbox.id = `checkbox-${file.name}`;
      checkbox.dataset.filename = file.name;
      checkbox.checked = selectedFiles.has(file.name);
      item.appendChild(checkbox);

      const checkboxLabel = document.createElement('label');
      checkboxLabel.htmlFor = `checkbox-${file.name}`;
      checkboxLabel.className = 'custom-checkbox-label';
      item.appendChild(checkboxLabel);

      // Media preview
      const previewContainer = document.createElement('div');
      
      if (ALLOWED_IMAGE_TYPES.includes(file.type)) {
        previewContainer.classList.add('file-preview-container');
        const img = document.createElement('img');
        img.src = `/api/media/${file.name}?${Date.now()}`;
        img.alt = file.name;
        img.classList.add('file-preview');
        img.loading = 'lazy';
        img.onerror = () => {
          img.src = PLACEHOLDER_IMAGE;
          img.style.opacity = '0.7';
          if (window.App?.Alerts?.show) {
            window.App.Alerts.show('Could not load preview image', 'warning');
          }
        };
        previewContainer.appendChild(img);
      } else {
        previewContainer.classList.add('file-icon');
        const icon = document.createElement('i');
        icon.className = 'fas fa-file-video';
        previewContainer.appendChild(icon);
      }

      // Click handler for preview
      previewContainer.addEventListener('click', (e) => {
        if (e.target?.tagName !== 'INPUT' && e.target?.tagName !== 'LABEL') {
          showPreview({
            ...file,
            path: `/api/media/${file.name}`
          });
        }
      });

      item.appendChild(previewContainer);

      // Filename
      const fileNameDiv = document.createElement('div');
      fileNameDiv.classList.add('file-name');
      fileNameDiv.textContent = file.name;
      item.appendChild(fileNameDiv);

      mediaGallery.appendChild(item);
    });

    // Add event listeners to checkboxes
    document.querySelectorAll('.file-checkbox').forEach(checkbox => {
      checkbox.addEventListener('change', function() {
        const filename = this.dataset.filename;
        if (this.checked) {
          selectedFiles.add(filename);
        } else {
          selectedFiles.delete(filename);
        }
        toggleDeleteButton(selectedFiles.size > 0);
      });
    });
  }

  function toggleDeleteButton(enabled) {
    if (deleteBtn) {
      deleteBtn.disabled = !enabled;
    }
  }

  function toggleSelectAllButton(enabled) {
    if (selectAllBtn) {
      selectAllBtn.disabled = !enabled;
    }
  }

  // File preview
  function showPreview(file) {
    if (!file || !previewContainer || !previewFilename || !previewFilesize || !previewDate) return;
    
    previewFilename.textContent = file.name || 'Unnamed file';
    previewFilesize.textContent = formatFileSize(file.size);
    previewDate.textContent = formatDate(file.date);
    
    previewContainer.innerHTML = '';
    
    if (ALLOWED_IMAGE_TYPES.includes(file.type)) {
      const img = document.createElement('img');
      img.src = `/api/media/${file.name}`;
      img.alt = file.name;
      img.style.maxWidth = '100%';
      img.style.maxHeight = '70vh';
      img.onerror = () => {
        img.src = PLACEHOLDER_IMAGE;
        img.style.opacity = '0.7';
        if (window.App?.Alerts?.show) {
          window.App.Alerts.show('Could not load preview image', 'warning');
        }
      };
      previewContainer.appendChild(img);
    } else if (ALLOWED_VIDEO_TYPES.includes(file.type)) {
      const video = document.createElement('video');
      video.controls = true;
      video.autoplay = true;
      video.style.maxWidth = '100%';
      video.style.maxHeight = '70vh';
      const source = document.createElement('source');
      source.src = `/api/media/${file.name}`;
      source.type = file.mimetype || `video/${file.type}`;
      video.appendChild(source);
      previewContainer.appendChild(video);
    }
    
    if (previewModal) {
      previewModal.style.display = 'block';
      document.body.style.overflow = 'hidden';
    }
  }

  function closePreview() {
    if (previewModal) {
      previewModal.style.display = 'none';
      document.body.style.overflow = 'auto';
      
      // Pause any playing video
      const video = previewContainer?.querySelector('video');
      if (video) {
        video.pause();
      }
    }
  }

  // Select all files
  function selectAllFiles() {
    const checkboxes = document.querySelectorAll('.file-checkbox');
    if (!checkboxes.length) return;
    
    const allSelected = Array.from(checkboxes).every(cb => cb.checked);
    
    checkboxes.forEach(checkbox => {
      checkbox.checked = !allSelected;
      const event = new Event('change');
      checkbox.dispatchEvent(event);
    });
    
    if (selectAllBtn) {
      selectAllBtn.innerHTML = allSelected ? 
        '<i class="fas fa-check-square"></i> Select All' : 
        '<i class="fas fa-times"></i> Deselect All';
    }
  }

  // Handle file upload
  async function uploadMedia() {
    if (!fileInput?.files || fileInput.files.length === 0) {
      if (window.App?.Alerts?.show) {
        window.App.Alerts.show('Please select files to upload', 'warning');
      }
      return;
    }

    const formData = new FormData();
    const csrfToken = document.querySelector('input[name="csrf_token"]')?.value;
    
    // Add CSRF token
    if (csrfToken) {
        formData.append('csrf_token', csrfToken);
    }
    
    let validFilesCount = 0;
    // Filter and validate files
    Array.from(fileInput.files).forEach(file => {
      if (isValidFile(file)) {
        formData.append('files', file);
        validFilesCount++;
      } else {
        console.warn(`Skipped invalid file: ${file.name}`);
      }
    });

    if (validFilesCount === 0) {
      if (window.App?.Alerts?.show) {
        window.App.Alerts.show('No valid files to upload (allowed: images and videos <50MB)', 'error');
      }
      return;
    }

    try {
      setButtonLoading(uploadBtn, true);
      
      const response = await fetch('/api/media/upload', {
        method: 'POST',
        body: formData,
        headers: {
            'X-CSRFToken': csrfToken
        }
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData?.error || `Upload failed with status ${response.status}`);
      }

      const result = await response.json();
      if (window.App?.Alerts?.show) {
        window.App.Alerts.show(`Uploaded ${result.files?.length || 0} file(s) successfully`, 'success');
      }
      if (fileInput) fileInput.value = '';
      await loadMediaFiles();
    } catch (error) {
      console.error('Upload error:', error);
      if (window.App?.Alerts?.show) {
        window.App.Alerts.show(`Upload failed: ${error.message}`, 'error');
      }
    } finally {
      setButtonLoading(uploadBtn, false);
    }
  }

  // Handle file deletion
  async function deleteSelectedMedia() {
    if (selectedFiles.size === 0) {
      if (window.App?.Alerts?.show) {
        window.App.Alerts.show('Please select files to delete', 'warning');
      }
      return;
    }

    if (!confirm(`Delete ${selectedFiles.size} selected file(s)? This cannot be undone.`)) {
      return;
    }

    try {
      setButtonLoading(deleteBtn, true);
      
      const response = await fetch('/api/media/files', {
        method: 'POST',
        headers: { 
            'Content-Type': 'application/json',
            'X-CSRFToken': document.querySelector('input[name="csrf_token"]')?.value
        },
        body: JSON.stringify({
            files: Array.from(selectedFiles),
            csrf_token: document.querySelector('input[name="csrf_token"]')?.value
        })
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData?.error || `Delete failed with status ${response.status}`);
      }

      if (window.App?.Alerts?.show) {
        window.App.Alerts.show(`Deleted ${selectedFiles.size} file(s) successfully`, 'success');
      }
      selectedFiles.clear();
      await loadMediaFiles();
    } catch (error) {
      console.error('Delete error:', error);
      if (window.App?.Alerts?.show) {
        window.App.Alerts.show(`Delete failed: ${error.message}`, 'error');
      }
    } finally {
      setButtonLoading(deleteBtn, false);
    }
  }

  function setButtonLoading(button, isLoading) {
    if (!button) return;
    
    if (isLoading) {
      button.disabled = true;
      button.innerHTML = button.id.includes('delete') ? 
        '<i class="fas fa-spinner fa-spin"></i> Deleting...' : 
        '<i class="fas fa-spinner fa-spin"></i> Uploading...';
    } else {
      button.disabled = false;
      button.innerHTML = button.id.includes('delete') ? 
        '<i class="fas fa-trash"></i> Delete Selected' : 
        '<i class="fas fa-upload"></i> Upload Files';
    }
  }

  // Initialize event listeners
  function initEventListeners() {
    if (uploadBtn) {
      uploadBtn.addEventListener('click', uploadMedia);
    }
    
    if (deleteBtn) {
      deleteBtn.addEventListener('click', deleteSelectedMedia);
    }

    if (selectAllBtn) {
      selectAllBtn.addEventListener('click', selectAllFiles);
    }

    if (searchInput) {
      searchInput.addEventListener('input', () => renderGallery(currentFiles));
    }

    if (sortSelect) {
      sortSelect.addEventListener('change', () => renderGallery(currentFiles));
    }

    if (groupSelect) {
      groupSelect.addEventListener('change', () => renderGallery(currentFiles));
    }

    if (closeModal) {
      closeModal.addEventListener('click', closePreview);
    }

    // Close modal when clicking outside
    window.addEventListener('click', (e) => {
      if (e.target === previewModal) {
        closePreview();
      }
    });

    // Close modal with ESC key
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && previewModal.style.display === 'block') {
        closePreview();
      }
    });

    // Reload files when page gains focus
    window.addEventListener('visibilitychange', () => {
      if (!document.hidden) loadMediaFiles();
    });

    // Initial load
    loadMediaFiles();
  }

  // Initialize the gallery
  initEventListeners();
})();
