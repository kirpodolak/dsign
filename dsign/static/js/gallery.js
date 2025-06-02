// Utility functions
function getFileExtension(filename) {
  if (!filename) return '';
  const parts = filename.split('.');
  return parts.length > 1 ? parts.pop().toLowerCase() : '';
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

// Constants
const ALLOWED_IMAGE_TYPES = ['jpg', 'jpeg', 'png', 'gif', 'webp'];
const ALLOWED_VIDEO_TYPES = ['mp4', 'webm', 'ogg', 'mov'];
const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50MB
const PLACEHOLDER_IMAGE = '/static/images/placeholder.jpg';

// Configuration
const GALLERY_CONFIG = {
  container: '#media-gallery',
  searchInput: '#search-input',
  sortSelect: '#sort-select',
  groupSelect: '#group-select',
  selectAllBtn: '#select-all',
  deleteSelectedBtn: '#delete-selected',
  uploadForm: '#upload-form',
  fileUploadInput: '#file-upload',
  uploadBtn: '#upload-btn',
  previewModal: '#preview-modal',
  previewContainer: '#preview-container',
  closeModalBtn: '.close-modal',
  filenameElement: '#preview-filename',
  filesizeElement: '#preview-filesize',
  dateElement: '#preview-date'
};

class MediaGallery {
  constructor(config = GALLERY_CONFIG) {
    this.config = config;
    this.elements = {};
    this.currentFiles = [];
    this.selectedFiles = new Set();
    
    this.initElements();
    this.initEventListeners();
    this.loadMediaFiles();
  }

  initElements() {
    for (const [key, selector] of Object.entries(this.config)) {
      this.elements[key] = document.querySelector(selector);
    }
  }

  isValidFile(file) {
    if (!file) return false;
    const ext = getFileExtension(file.name);
    return (ALLOWED_IMAGE_TYPES.includes(ext) || 
            ALLOWED_VIDEO_TYPES.includes(ext)) && 
           file.size <= MAX_FILE_SIZE;
  }

  async loadMediaFiles() {
    try {
      const playlist_id = new URLSearchParams(window.location.search).get('playlist_id') || 'all';
      const url = `/api/media/files?playlist_id=${playlist_id}`;
    
      const response = await fetch(url, {
        headers: { 'Accept': 'application/json' },
        credentials: 'include'
      });

      if (response.redirected) {
        window.location.href = '/api/auth/login';
        return;
      }

      if (!response.ok) {
        throw new Error(`Server returned ${response.status} status`);
      }

      const data = await response.json();
    
      if (!data?.success) {
        throw new Error(data?.error || 'Invalid response format');
      }

      this.currentFiles = data.files.map(file => ({
        name: file.filename,
        type: file.type || getFileExtension(file.filename),
        date: file.modified || Date.now(),
        size: file.size || 0,
        path: `/api/media/${encodeURIComponent(file.filename)}`,
        mimetype: file.mimetype,
        included: file.included || false,
        is_video: file.is_video || false
      }));

      this.renderGallery(this.currentFiles);
    } catch (error) {
      console.error('Failed to load media files:', error);
      if (window.App?.Alerts?.show) {
        window.App.Alerts.show(`Error loading files: ${error.message}`, 'error');
      }
    }
  }

  processFiles(files) {
    if (!Array.isArray(files)) return [];
    
    let filtered = [...files];
    if (this.elements.searchInput?.value) {
      const searchTerm = this.elements.searchInput.value.toLowerCase().trim();
      filtered = filtered.filter(file => 
        file.name?.toLowerCase().includes(searchTerm)
      );
    }

    const sortValue = this.elements.sortSelect?.value;
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

  groupFiles(files) {
    if (!Array.isArray(files)) return [];
    const groupValue = this.elements.groupSelect?.value;
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

  renderGallery(files) {
    if (!this.elements.container) return;
    
    this.elements.container.innerHTML = '';
    
    if (!Array.isArray(files) || files.length === 0) {
      this.elements.container.innerHTML = '<p class="empty-message">No media files found.</p>';
      this.toggleDeleteButton(false);
      this.toggleSelectAllButton(false);
      return;
    }

    const processedFiles = this.processFiles(files);
    const groupedFiles = this.groupFiles(processedFiles);

    if (Array.isArray(groupedFiles)) {
      this.renderFiles(groupedFiles);
    } else {
      Object.entries(groupedFiles).forEach(([groupName, groupFiles]) => {
        const groupHeader = document.createElement('div');
        groupHeader.className = 'group-header';
        groupHeader.textContent = groupName;
        this.elements.container.appendChild(groupHeader);
        this.renderFiles(groupFiles);
      });
    }

    this.toggleDeleteButton(this.selectedFiles.size > 0);
    this.toggleSelectAllButton(processedFiles.length > 0);
  }

  renderFiles(files) {
    if (!Array.isArray(files)) return;

    files.forEach(file => {
      if (!file) return;
      
      const item = document.createElement('div');
      item.classList.add('file-item');
      item.dataset.filename = file.name;

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.className = 'file-checkbox';
      checkbox.id = `checkbox-${file.name}`;
      checkbox.dataset.filename = file.name;
      checkbox.checked = this.selectedFiles.has(file.name);
      item.appendChild(checkbox);

      const checkboxLabel = document.createElement('label');
      checkboxLabel.htmlFor = `checkbox-${file.name}`;
      checkboxLabel.className = 'custom-checkbox-label';
      item.appendChild(checkboxLabel);

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

      previewContainer.addEventListener('click', (e) => {
        if (e.target?.tagName !== 'INPUT' && e.target?.tagName !== 'LABEL') {
          this.showPreview({
            ...file,
            path: `/api/media/${file.name}`
          });
        }
      });

      item.appendChild(previewContainer);

      const fileNameDiv = document.createElement('div');
      fileNameDiv.classList.add('file-name');
      fileNameDiv.textContent = file.name;
      item.appendChild(fileNameDiv);

      this.elements.container.appendChild(item);
    });

    document.querySelectorAll('.file-checkbox').forEach(checkbox => {
      checkbox.addEventListener('change', function() {
        const filename = this.dataset.filename;
        if (this.checked) {
          this.selectedFiles.add(filename);
        } else {
          this.selectedFiles.delete(filename);
        }
        this.toggleDeleteButton(this.selectedFiles.size > 0);
      }.bind(this));
    });
  }

  toggleDeleteButton(enabled) {
    if (this.elements.deleteSelectedBtn) {
      this.elements.deleteSelectedBtn.disabled = !enabled;
    }
  }

  toggleSelectAllButton(enabled) {
    if (this.elements.selectAllBtn) {
      this.elements.selectAllBtn.disabled = !enabled;
    }
  }

  showPreview(file) {
    if (!file || !this.elements.previewContainer || 
        !this.elements.filenameElement || !this.elements.filesizeElement || 
        !this.elements.dateElement) return;
    
    this.elements.filenameElement.textContent = file.name || 'Unnamed file';
    this.elements.filesizeElement.textContent = formatFileSize(file.size);
    this.elements.dateElement.textContent = formatDate(file.date);
    
    this.elements.previewContainer.innerHTML = '';
    
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
      this.elements.previewContainer.appendChild(img);
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
      this.elements.previewContainer.appendChild(video);
    }
    
    if (this.elements.previewModal) {
      this.elements.previewModal.style.display = 'block';
      document.body.style.overflow = 'hidden';
    }
  }

  closePreview() {
    if (this.elements.previewModal) {
      this.elements.previewModal.style.display = 'none';
      document.body.style.overflow = 'auto';
      
      const video = this.elements.previewContainer?.querySelector('video');
      if (video) {
        video.pause();
      }
    }
  }

  selectAllFiles() {
    const checkboxes = document.querySelectorAll('.file-checkbox');
    if (!checkboxes.length) return;
    
    const allSelected = Array.from(checkboxes).every(cb => cb.checked);
    
    checkboxes.forEach(checkbox => {
      checkbox.checked = !allSelected;
      const event = new Event('change');
      checkbox.dispatchEvent(event);
    });
    
    if (this.elements.selectAllBtn) {
      this.elements.selectAllBtn.innerHTML = allSelected ? 
        '<i class="fas fa-check-square"></i> Select All' : 
        '<i class="fas fa-times"></i> Deselect All';
    }
  }

  async uploadMedia() {
    if (!this.elements.fileUploadInput?.files || this.elements.fileUploadInput.files.length === 0) {
      if (window.App?.Alerts?.show) {
        window.App.Alerts.show('Please select files to upload', 'warning');
      }
      return;
    }

    const formData = new FormData();
    const csrfToken = document.querySelector('input[name="csrf_token"]')?.value;
    
    if (csrfToken) {
        formData.append('csrf_token', csrfToken);
    }
    
    let validFilesCount = 0;
    Array.from(this.elements.fileUploadInput.files).forEach(file => {
      if (this.isValidFile(file)) {
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
      this.setButtonLoading(this.elements.uploadBtn, true);
      
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
      if (this.elements.fileUploadInput) this.elements.fileUploadInput.value = '';
      await this.loadMediaFiles();
    } catch (error) {
      console.error('Upload error:', error);
      if (window.App?.Alerts?.show) {
        window.App.Alerts.show(`Upload failed: ${error.message}`, 'error');
      }
    } finally {
      this.setButtonLoading(this.elements.uploadBtn, false);
    }
  }

  async deleteSelectedMedia() {
    if (this.selectedFiles.size === 0) {
      if (window.App?.Alerts?.show) {
        window.App.Alerts.show('Please select files to delete', 'warning');
      }
      return;
    }

    if (!confirm(`Delete ${this.selectedFiles.size} selected file(s)? This cannot be undone.`)) {
      return;
    }

    try {
      this.setButtonLoading(this.elements.deleteSelectedBtn, true);
      
      const response = await fetch('/api/media/files', {
        method: 'POST',
        headers: { 
            'Content-Type': 'application/json',
            'X-CSRFToken': document.querySelector('input[name="csrf_token"]')?.value
        },
        body: JSON.stringify({
            files: Array.from(this.selectedFiles),
            csrf_token: document.querySelector('input[name="csrf_token"]')?.value
        })
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData?.error || `Delete failed with status ${response.status}`);
      }

      if (window.App?.Alerts?.show) {
        window.App.Alerts.show(`Deleted ${this.selectedFiles.size} file(s) successfully`, 'success');
      }
      this.selectedFiles.clear();
      await this.loadMediaFiles();
    } catch (error) {
      console.error('Delete error:', error);
      if (window.App?.Alerts?.show) {
        window.App.Alerts.show(`Delete failed: ${error.message}`, 'error');
      }
    } finally {
      this.setButtonLoading(this.elements.deleteSelectedBtn, false);
    }
  }

  setButtonLoading(button, isLoading) {
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

  initEventListeners() {
    if (this.elements.uploadBtn) {
      this.elements.uploadBtn.addEventListener('click', this.uploadMedia.bind(this));
    }
    
    if (this.elements.deleteSelectedBtn) {
      this.elements.deleteSelectedBtn.addEventListener('click', this.deleteSelectedMedia.bind(this));
    }

    if (this.elements.selectAllBtn) {
      this.elements.selectAllBtn.addEventListener('click', this.selectAllFiles.bind(this));
    }

    if (this.elements.searchInput) {
      this.elements.searchInput.addEventListener('input', () => this.renderGallery(this.currentFiles));
    }

    if (this.elements.sortSelect) {
      this.elements.sortSelect.addEventListener('change', () => this.renderGallery(this.currentFiles));
    }

    if (this.elements.groupSelect) {
      this.elements.groupSelect.addEventListener('change', () => this.renderGallery(this.currentFiles));
    }

    if (this.elements.closeModalBtn) {
      this.elements.closeModalBtn.addEventListener('click', this.closePreview.bind(this));
    }

    window.addEventListener('click', (e) => {
      if (e.target === this.elements.previewModal) {
        this.closePreview();
      }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.elements.previewModal.style.display === 'block') {
        this.closePreview();
      }
    });

    window.addEventListener('visibilitychange', () => {
      if (!document.hidden) this.loadMediaFiles();
    });
  }
}

// Initialize the gallery
function initializeGallery() {
  try {
    const gallery = new MediaGallery();
    window.App = window.App || {};
    window.App.MediaGallery = gallery;
    console.log('MediaGallery initialized successfully');
    return gallery;
  } catch (error) {
    console.error('Failed to initialize MediaGallery:', error);
    if (window.App?.Alerts?.show) {
      window.App.Alerts.show(
        'Gallery Error', 
        'Failed to initialize media gallery. Please try again later.',
        'error'
      );
    }
    return null;
  }
}

// Smart initialization handler
function initGalleryWhenReady() {
  // First try standard DOM ready check
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    initializeGallery();
  } 
  // Fallback to DOMContentLoaded
  else {
    document.addEventListener('DOMContentLoaded', initializeGallery);
  }
}

// Main entry point
(function() {
  // Check if App.onReady exists
  if (window.App && typeof window.App.onReady === 'function') {
    window.App.onReady(initializeGallery);
  } else {
    // Use standard initialization
    initGalleryWhenReady();
  }
})();

export { MediaGallery, initializeGallery };
