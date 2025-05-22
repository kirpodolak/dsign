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
const ALLOWED_IMAGE_TYPES = ['jpg', 'jpeg', 'png', 'gif'];
const ALLOWED_VIDEO_TYPES = ['mp4', 'webm', 'ogg'];
const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50MB
const PLACEHOLDER_IMAGE = '/static/images/placeholder.jpg';

export class MediaGallery {
  constructor() {
    // DOM Elements
    this.mediaGallery = document.getElementById('media-gallery');
    this.uploadForm = document.getElementById('upload-form');
    this.fileInput = document.getElementById('file-upload');
    this.uploadBtn = document.getElementById('upload-btn');
    this.deleteBtn = document.getElementById('delete-selected');
    this.selectAllBtn = document.getElementById('select-all');
    this.searchInput = document.getElementById('search-input');
    this.sortSelect = document.getElementById('sort-select');
    this.groupSelect = document.getElementById('group-select');
    this.previewModal = document.getElementById('preview-modal');
    this.closeModal = document.querySelector('.close-modal');
    this.previewContainer = document.getElementById('preview-container');
    this.previewFilename = document.getElementById('preview-filename');
    this.previewFilesize = document.getElementById('preview-filesize');
    this.previewDate = document.getElementById('preview-date');

    // State
    this.currentFiles = [];
    this.selectedFiles = new Set();

    this.initEventListeners();
  }

  isValidFile(file) {
    if (!file) return false;
    const ext = getFileExtension(file.name);
    return (ALLOWED_IMAGE_TYPES.includes(ext) || 
            ALLOWED_VIDEO_TYPES.includes(ext)) && 
           file.size <= MAX_FILE_SIZE;
  }

  // Load media files from server
  async loadMediaFiles() {
    try {
      const playlist_id = new URLSearchParams(window.location.search).get('playlist_id') || 'all';
      const url = `/api/media/files?playlist_id=${playlist_id}`;
    
      const response = await fetch(url, {
        headers: { 'Accept': 'application/json' },
        credentials: 'include'
      });

      if (response.redirected) {
        window.location.href = '/auth/login';
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

  // Filter and sort files
  processFiles(files) {
    if (!Array.isArray(files)) return [];
    
    // Filter by search
    let filtered = [...files];
    if (this.searchInput?.value) {
      const searchTerm = this.searchInput.value.toLowerCase().trim();
      filtered = filtered.filter(file => 
        file.name?.toLowerCase().includes(searchTerm)
      );
    }

    // Sort
    const sortValue = this.sortSelect?.value;
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
  groupFiles(files) {
    if (!Array.isArray(files)) return [];
    const groupValue = this.groupSelect?.value;
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
  renderGallery(files) {
    if (!this.mediaGallery) return;
    
    this.mediaGallery.innerHTML = '';
    
    if (!Array.isArray(files) || files.length === 0) {
      this.mediaGallery.innerHTML = '<p class="empty-message">No media files found.</p>';
      this.toggleDeleteButton(false);
      this.toggleSelectAllButton(false);
      return;
    }

    const processedFiles = this.processFiles(files);
    const groupedFiles = this.groupFiles(processedFiles);

    if (Array.isArray(groupedFiles)) {
      this.renderFiles(groupedFiles);
    } else {
      // Render grouped files
      Object.entries(groupedFiles).forEach(([groupName, groupFiles]) => {
        const groupHeader = document.createElement('div');
        groupHeader.className = 'group-header';
        groupHeader.textContent = groupName;
        this.mediaGallery.appendChild(groupHeader);
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

      // Checkbox
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
          this.showPreview({
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

      this.mediaGallery.appendChild(item);
    });

    // Add event listeners to checkboxes
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
    if (this.deleteBtn) {
      this.deleteBtn.disabled = !enabled;
    }
  }

  toggleSelectAllButton(enabled) {
    if (this.selectAllBtn) {
      this.selectAllBtn.disabled = !enabled;
    }
  }

  // File preview
  showPreview(file) {
    if (!file || !this.previewContainer || !this.previewFilename || !this.previewFilesize || !this.previewDate) return;
    
    this.previewFilename.textContent = file.name || 'Unnamed file';
    this.previewFilesize.textContent = formatFileSize(file.size);
    this.previewDate.textContent = formatDate(file.date);
    
    this.previewContainer.innerHTML = '';
    
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
      this.previewContainer.appendChild(img);
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
      this.previewContainer.appendChild(video);
    }
    
    if (this.previewModal) {
      this.previewModal.style.display = 'block';
      document.body.style.overflow = 'hidden';
    }
  }

  closePreview() {
    if (this.previewModal) {
      this.previewModal.style.display = 'none';
      document.body.style.overflow = 'auto';
      
      // Pause any playing video
      const video = this.previewContainer?.querySelector('video');
      if (video) {
        video.pause();
      }
    }
  }

  // Select all files
  selectAllFiles() {
    const checkboxes = document.querySelectorAll('.file-checkbox');
    if (!checkboxes.length) return;
    
    const allSelected = Array.from(checkboxes).every(cb => cb.checked);
    
    checkboxes.forEach(checkbox => {
      checkbox.checked = !allSelected;
      const event = new Event('change');
      checkbox.dispatchEvent(event);
    });
    
    if (this.selectAllBtn) {
      this.selectAllBtn.innerHTML = allSelected ? 
        '<i class="fas fa-check-square"></i> Select All' : 
        '<i class="fas fa-times"></i> Deselect All';
    }
  }

  // Handle file upload
  async uploadMedia() {
    if (!this.fileInput?.files || this.fileInput.files.length === 0) {
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
    Array.from(this.fileInput.files).forEach(file => {
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
      this.setButtonLoading(this.uploadBtn, true);
      
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
      if (this.fileInput) this.fileInput.value = '';
      await this.loadMediaFiles();
    } catch (error) {
      console.error('Upload error:', error);
      if (window.App?.Alerts?.show) {
        window.App.Alerts.show(`Upload failed: ${error.message}`, 'error');
      }
    } finally {
      this.setButtonLoading(this.uploadBtn, false);
    }
  }

  // Handle file deletion
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
      this.setButtonLoading(this.deleteBtn, true);
      
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
      this.setButtonLoading(this.deleteBtn, false);
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

  // Initialize event listeners
  initEventListeners() {
    if (this.uploadBtn) {
      this.uploadBtn.addEventListener('click', this.uploadMedia.bind(this));
    }
    
    if (this.deleteBtn) {
      this.deleteBtn.addEventListener('click', this.deleteSelectedMedia.bind(this));
    }

    if (this.selectAllBtn) {
      this.selectAllBtn.addEventListener('click', this.selectAllFiles.bind(this));
    }

    if (this.searchInput) {
      this.searchInput.addEventListener('input', () => this.renderGallery(this.currentFiles));
    }

    if (this.sortSelect) {
      this.sortSelect.addEventListener('change', () => this.renderGallery(this.currentFiles));
    }

    if (this.groupSelect) {
      this.groupSelect.addEventListener('change', () => this.renderGallery(this.currentFiles));
    }

    if (this.closeModal) {
      this.closeModal.addEventListener('click', this.closePreview.bind(this));
    }

    // Close modal when clicking outside
    window.addEventListener('click', (e) => {
      if (e.target === this.previewModal) {
        this.closePreview();
      }
    });

    // Close modal with ESC key
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.previewModal.style.display === 'block') {
        this.closePreview();
      }
    });

    // Reload files when page gains focus
    window.addEventListener('visibilitychange', () => {
      if (!document.hidden) this.loadMediaFiles();
    });

    // Initial load
    this.loadMediaFiles();
  }
}

// Initialize the gallery when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  const mediaGallery = new MediaGallery();
  
  // For backward compatibility with other modules
  window.App = window.App || {};
  window.App.MediaGallery = mediaGallery;
});

export default MediaGallery;
