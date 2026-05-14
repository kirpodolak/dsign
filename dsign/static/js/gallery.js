import { t, getUiLang, applyI18n } from './i18n.js';

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

function safeDomId(prefix, name) {
  const safe = String(name).replace(/[^a-zA-Z0-9_-]/g, '_');
  return `${prefix}-${safe}`;
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
const ALLOWED_VIDEO_TYPES = ['mp4', 'webm', 'ogg', 'mov', 'avi'];
const MAX_FILE_SIZE = 1024 * 1024 * 1024; // 1 GiB, согласовано с Config.MAX_UPLOAD_BYTES / FileService
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
  closeModalBtn: '#preview-modal-close',
  filenameElement: '#preview-filename',
  previewFolderElement: '#preview-folder',
  filesizeElement: '#preview-filesize',
  dateElement: '#preview-date',
  folderNav: '#gallery-folder-nav',
  folderColumn: '#gallery-folder-column',
  folderListScroller: '#gallery-folder-list-scroller',
  newFolderNameInput: '#gallery-new-folder-name',
  createFolderBtn: '#gallery-create-folder-btn',
  viewAllBtn: '#gallery-view-all',
  viewByFolderBtn: '#gallery-view-by-folder',
  moveSelectedBtn: '#gallery-move-selected-btn',
  moveModal: '#gallery-move-modal',
  moveModalSelect: '#gallery-move-modal-folder-select',
  moveModalConfirm: '#gallery-move-modal-confirm',
  moveModalCancel: '#gallery-move-modal-cancel',
};

class MediaGallery {
  constructor(config = GALLERY_CONFIG) {
    if (MediaGallery.instance) {
      return MediaGallery.instance;
    }
    this.config = config;
    this.elements = {};
    this.currentFiles = [];
    this.transcodeStatus = {};
    this.transcodePollTimer = null;
    this.selectedFiles = new Set();
    this.viewMode = 'all';
    /** In by_folder view: null = unsorted (no meta row), number = folder id */
    this.folderTargetId = null;
    this._searchReloadTimer = null;
    /** @type {Array<{id:number,name:string}>} */
    this._foldersCache = [];

    this.initElements();
    this.initEventListeners();
    this._syncFolderColumnState();
    this._syncViewToggleButtons();
    void this._renderFolderNav();
    this.loadMediaFiles();
    this._syncMoveToolbarBtn();
    MediaGallery.instance = this;
  }

  /**
   * Gallery grid should be fast: use server-side cached thumbnails.
   * Original media is used only in the preview modal.
   */
  getThumbnailUrl(filename) {
    return `/api/media/thumbnail/${encodeURIComponent(filename)}`;
  }

  getMediaUrl(filename) {
    return `/api/media/${encodeURIComponent(filename)}`;
  }

  getExternalProvider(file) {
    const provider = file?.external?.provider || '';
    if (provider === 'vkvideo') return { key: 'vkvideo', label: 'VK Video' };
    if (provider === 'rutube') return { key: 'rutube', label: 'Rutube' };
    return provider ? { key: provider, label: provider } : null;
  }

  isExternalFile(file) {
    return Boolean(file?.is_external || (file?.external && typeof file.external === 'object'));
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
      const params = new URLSearchParams();
      if (this.viewMode === 'by_folder') {
        params.set('view', 'by_folder');
        if (this.folderTargetId != null) {
          params.set('folder_id', String(this.folderTargetId));
        }
      } else {
        params.set('view', 'all');
      }
      const sort = this.elements.sortSelect?.value || 'name-asc';
      params.set('sort', sort);
      const q = (this.elements.searchInput?.value || '').trim();
      if (q) params.set('search', q);

      const url = `/api/media/files?${params.toString()}`;
    
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
        is_video: file.is_video || false,
        is_external: Boolean(file.is_external),
        external: file.external || null,
        folder_id: file.folder_id ?? null,
        folder_name: file.folder_name ?? null,
      }));

      // Merge transcode status (best-effort)
      await this.refreshTranscodeStatus({ startPolling: true });
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
    return [...files];
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
      const st = this.transcodeStatus?.[file.name];
      if (st && st.state === 'running') {
        item.dataset.transcoding = 'true';
      } else {
        delete item.dataset.transcoding;
      }

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.className = 'file-checkbox';
      const cbId = safeDomId('cb', file.name);
      checkbox.id = cbId;
      checkbox.dataset.filename = file.name;
      checkbox.checked = this.selectedFiles.has(file.name);
      item.appendChild(checkbox);

      const checkboxLabel = document.createElement('label');
      checkboxLabel.htmlFor = cbId;
      checkboxLabel.className = 'custom-checkbox-label';
      item.appendChild(checkboxLabel);

      const previewContainer = document.createElement('div');
      
      const isExternal = this.isExternalFile(file);
      const provider = isExternal ? this.getExternalProvider(file) : null;
      const isVideo =
        Boolean(file?.is_video) ||
        ALLOWED_VIDEO_TYPES.includes(String(file.type || '').toLowerCase()) ||
        String(file.name || '').toLowerCase().startsWith('ext-');

      if (ALLOWED_IMAGE_TYPES.includes(file.type) && !isExternal) {
        previewContainer.classList.add('file-preview-container');
        const img = document.createElement('img');
        img.src = `/api/media/${encodeURIComponent(file.name)}?${Date.now()}`;
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
        // Videos: always try server-side thumbnail cache first (local + external).
        if (isVideo) {
          previewContainer.classList.add('file-preview-container', 'file-preview-container--video');
          const img = document.createElement('img');
          img.src = this.getThumbnailUrl(file.name);
          img.alt = file.external?.title || file.name;
          img.classList.add('file-preview');
          img.loading = 'lazy';
          img.decoding = 'async';
          img.onerror = () => {
            // Fallback placeholder if thumbnail is unavailable for this video.
            img.src = PLACEHOLDER_IMAGE;
            img.style.opacity = '0.7';
          };
          previewContainer.appendChild(img);
        } else {
          // Other non-image files: placeholder icon.
          previewContainer.classList.add('file-icon');
          const icon = document.createElement('span');
          icon.className = 'file-icon__glyph';
          icon.setAttribute('aria-hidden', 'true');
          icon.textContent = '📄';
          previewContainer.appendChild(icon);
        }
      }

      const folderPin = document.createElement('span');
      folderPin.className =
        'file-folder-pin' + (!file.folder_name ? ' file-folder-pin--unsorted' : '');
      const lang = getUiLang();
      folderPin.title = file.folder_name
        ? `${t('gallery_folder_label', lang)}: ${file.folder_name}`
        : `${t('gallery_folder_label', lang)}: ${t('pl_filter_unsorted', lang)}`;
      folderPin.setAttribute('aria-hidden', 'true');
      folderPin.innerHTML = '<span class="file-folder-pin__glyph">📁</span>';
      previewContainer.appendChild(folderPin);

      if (provider) {
        const badge = document.createElement('div');
        badge.className = `provider-badge provider-badge--${provider.key}`;
        badge.textContent = provider.label;
        item.appendChild(badge);
      }

      previewContainer.addEventListener('click', (e) => {
        if (e.target.closest('.file-folder-pin')) return;
        if (e.target?.tagName !== 'INPUT' && e.target?.tagName !== 'LABEL') {
          this.showPreview({
            ...file,
            path: `/api/media/${encodeURIComponent(file.name)}`
          });
        }
      });

      item.appendChild(previewContainer);

      // Transcode overlay
      if (st && (st.state === 'queued' || st.state === 'running' || st.state === 'failed')) {
        const overlay = document.createElement('div');
        overlay.className = 'transcode-overlay';
        overlay.dataset.filename = file.name;
        const meta = document.createElement('div');
        meta.className = 'transcode-overlay__meta';
        const bar = document.createElement('div');
        bar.className = 'transcode-overlay__bar';
        const fill = document.createElement('div');
        fill.className = 'transcode-overlay__fill';
        bar.appendChild(fill);
        overlay.appendChild(meta);
        overlay.appendChild(bar);
        item.appendChild(overlay);
        this._applyTranscodeOverlay(overlay, st);
      }

      const fileNameDiv = document.createElement('div');
      fileNameDiv.classList.add('file-name');
      fileNameDiv.textContent = (file.external?.title || file.name);
      item.appendChild(fileNameDiv);

      this.elements.container.appendChild(item);
    });

    document.querySelectorAll('.file-checkbox').forEach((checkbox) => {
      checkbox.addEventListener('change', () => {
        const filename = checkbox.dataset.filename;
        if (!filename) return;
        if (checkbox.checked) {
          this.selectedFiles.add(filename);
        } else {
          this.selectedFiles.delete(filename);
        }
        this.toggleDeleteButton(this.selectedFiles.size > 0);
        this._syncMoveToolbarBtn();
      });
    });
  }

  _formatEta(sec) {
    if (sec == null || !Number.isFinite(sec) || sec < 0) return '';
    const s = Math.round(sec);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}m ${r}s`;
  }

  _applyTranscodeOverlay(overlayEl, st) {
    if (!overlayEl || !st) return;
    const meta = overlayEl.querySelector('.transcode-overlay__meta');
    const fill = overlayEl.querySelector('.transcode-overlay__fill');
    const pct = Number(st.percent || 0);
    if (fill) fill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
    if (meta) {
      if (st.state === 'failed') {
        meta.innerHTML = `<strong>Transcode failed</strong>`;
      } else if (st.state === 'queued') {
        meta.innerHTML = `<strong>Queued for optimization…</strong>`;
      } else {
        const eta = this._formatEta(st.eta_sec);
        meta.innerHTML = `<strong>Optimizing video…</strong> ${Math.round(pct)}%${eta ? ` · ETA ${eta}` : ''}`;
      }
    }
  }

  async refreshTranscodeStatus({ startPolling = false } = {}) {
    try {
      const resp = await fetch('/api/media/transcode/status', {
        headers: { 'Accept': 'application/json' },
        credentials: 'include'
      });
      if (!resp.ok) return;
      const data = await resp.json();
      if (!data?.success) return;
      this.transcodeStatus = data.status || {};

      // Update existing overlays without re-rendering everything.
      document.querySelectorAll('.transcode-overlay').forEach((el) => {
        const fn = el.dataset.filename;
        if (!fn) return;
        const st = this.transcodeStatus?.[fn];
        if (!st || st.state === 'completed') {
          el.remove();
          const parent = el.closest('.file-item');
          if (parent) delete parent.dataset.transcoding;
          return;
        }
        const parent = el.closest('.file-item');
        if (parent) parent.dataset.transcoding = st.state === 'running' ? 'true' : '';
        this._applyTranscodeOverlay(el, st);
      });

      if (startPolling) {
        const anyRunning = Object.values(this.transcodeStatus || {}).some(s => s && s.state === 'running');
        if (anyRunning && !this.transcodePollTimer) {
          this.transcodePollTimer = setInterval(() => {
            if (!document.hidden) this.refreshTranscodeStatus();
          }, 1500);
        }
        if (!anyRunning && this.transcodePollTimer) {
          clearInterval(this.transcodePollTimer);
          this.transcodePollTimer = null;
        }
      }
    } catch {
      // ignore
    }
  }

  toggleDeleteButton(enabled) {
    if (this.elements.deleteSelectedBtn) {
      this.elements.deleteSelectedBtn.disabled = !enabled;
    }
    this._syncMoveToolbarBtn();
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
    const pf = this.elements.previewFolderElement;
    if (pf) {
      const lang = getUiLang();
      if (file.folder_name) {
        pf.textContent = `${t('gallery_folder_label', lang)}: ${file.folder_name}`;
      } else {
        pf.textContent = `${t('gallery_folder_label', lang)}: ${t('pl_filter_unsorted', lang)}`;
      }
      pf.hidden = false;
    }
    this.elements.filesizeElement.textContent = formatFileSize(file.size);
    this.elements.dateElement.textContent = formatDate(file.date);
    
    this.elements.previewContainer.innerHTML = '';
    
    if (ALLOWED_IMAGE_TYPES.includes(file.type)) {
      const img = document.createElement('img');
      img.src = `/api/media/${encodeURIComponent(file.name)}`;
      img.alt = file.name;
      img.classList.add('preview-modal__img');
      img.onerror = () => {
        img.src = PLACEHOLDER_IMAGE;
        img.style.opacity = '0.7';
        if (window.App?.Alerts?.show) {
          window.App.Alerts.show('Could not load preview image', 'warning');
        }
      };
      this.elements.previewContainer.appendChild(img);
    } else if (
      ALLOWED_VIDEO_TYPES.includes(file.type) ||
      Boolean(file?.is_video) ||
      this.isExternalFile(file) ||
      String(file.name || '').toLowerCase().startsWith('ext-')
    ) {
      const isExternal = this.isExternalFile(file);
      const provider = isExternal ? this.getExternalProvider(file) : null;

      // Local videos can always be previewed via /api/media/<filename>.
      // External videos: don't attempt browser playback/embeds. Show a large thumbnail preview
      // (same as the gallery tile) and provide an "open original" action.
      if (!isExternal) {
        const video = document.createElement('video');
        video.controls = true;
        video.autoplay = true;
        video.playsInline = true;
        video.preload = 'metadata';
        const source = document.createElement('source');
        source.src = `/api/media/${encodeURIComponent(file.name)}`;
        source.type = file.mimetype || `video/${file.type}`;
        video.appendChild(source);
        this.elements.previewContainer.appendChild(video);
      } else {
        this._renderExternalPreviewFallback(file, provider);
      }
    }
    
    if (this.elements.moveModal && !this.elements.moveModal.hasAttribute('hidden')) {
      this._closeMoveModal();
    }

    if (this.elements.previewModal) {
      this.elements.previewModal.removeAttribute('hidden');
      document.body.style.overflow = 'hidden';
    }
  }

  _renderExternalPreviewFallback(file, provider) {
    const wrap = document.createElement('div');
    wrap.className = 'preview-external-fallback';

    const img = document.createElement('img');
    img.src = this.getThumbnailUrl(file.name);
    img.alt = file.external?.title || file.name;
    img.classList.add('preview-modal__img');
    img.loading = 'lazy';
    img.decoding = 'async';
    img.onerror = () => {
      img.src = PLACEHOLDER_IMAGE;
      img.style.opacity = '0.7';
    };
    wrap.appendChild(img);

    const actions = document.createElement('div');
    actions.className = 'preview-external-actions';

    const openBtn = document.createElement('a');
    openBtn.className = 'btn primary';
    openBtn.href = (file?.external?.url || file?.url || '').toString();
    openBtn.target = '_blank';
    openBtn.rel = 'noopener noreferrer';
    const label = provider?.label ? `Open ${provider.label}` : 'Open link';
    openBtn.textContent = label;
    actions.appendChild(openBtn);

    wrap.appendChild(actions);
    this.elements.previewContainer.appendChild(wrap);
  }

  _renderExternalInlineEmbed(file, provider) {
    const rawUrl = String(file?.external?.url || file?.path || '').trim();
    const prov = String(file?.external?.provider || provider?.key || '').toLowerCase();
    const url = this._buildEmbedUrl(rawUrl, prov);
    if (!url) {
      this._renderExternalPreviewFallback(file, provider);
      return;
    }

    const wrap = document.createElement('div');
    wrap.className = 'preview-external-embed';

    const iframe = document.createElement('iframe');
    iframe.className = 'preview-external-embed__frame';
    iframe.src = url;
    iframe.loading = 'lazy';
    iframe.referrerPolicy = 'no-referrer-when-downgrade';
    iframe.allow =
      prov === 'vkvideo'
        ? 'autoplay; encrypted-media; fullscreen; picture-in-picture; screen-wake-lock;'
        : 'clipboard-write; autoplay; fullscreen; picture-in-picture;';
    iframe.allowFullscreen = true;
    iframe.setAttribute('title', file.external?.title || url);
    wrap.appendChild(iframe);

    const footer = document.createElement('div');
    footer.className = 'preview-external-embed__footer';
    const btn = document.createElement('a');
    btn.href = rawUrl;
    btn.target = '_blank';
    btn.rel = 'noopener noreferrer';
    btn.className = 'btn secondary preview-external-embed__open';
    btn.textContent = provider?.label ? `Open on ${provider.label}` : 'Open link';
    footer.appendChild(btn);
    wrap.appendChild(footer);

    this.elements.previewContainer.appendChild(wrap);
  }

  /**
   * Build provider-specific embed URL from a canonical page URL or existing embed URL.
   * @param {string} url
   * @param {string} provider
   * @returns {string}
   */
  _buildEmbedUrl(url, provider) {
    const u = String(url || '').trim();
    if (!u) return '';
    // If user already gave an embed src, keep it.
    if (/\/play\/embed\//i.test(u) || /\/video_ext\.php/i.test(u) || /\/embed\//i.test(u)) {
      return u;
    }

    // Rutube: https://rutube.ru/video/<id>/ -> https://rutube.ru/play/embed/<id>/
    if (provider === 'rutube' || /rutube\.ru/i.test(u)) {
      const m = u.match(/rutube\.ru\/video\/([0-9a-f]{16,})/i);
      if (m && m[1]) return `https://rutube.ru/play/embed/${m[1]}/`;
      const m2 = u.match(/rutube\.ru\/(?:play\/)?embed\/([0-9a-f]{16,})/i);
      if (m2 && m2[1]) return `https://rutube.ru/play/embed/${m2[1]}/`;
      return u;
    }

    // VK Video: accept both vk.com/video and vkvideo.ru/video
    if (provider === 'vkvideo' || /vkvideo\.ru/i.test(u) || /vk\.com\/video/i.test(u)) {
      // vkvideo.ru/video-<oid>_<id>
      let m = u.match(/vkvideo\.ru\/video(-?\d+)_(\d+)/i);
      if (!m) m = u.match(/vk\.com\/video(-?\d+)_(\d+)/i);
      if (m && m[1] && m[2]) {
        const oid = m[1];
        const id = m[2];
        // Use vk.com embed endpoint (works for VK Video player)
        return `https://vk.com/video_ext.php?oid=${encodeURIComponent(oid)}&id=${encodeURIComponent(id)}&hd=2`;
      }
      return u;
    }

    return u;
  }

  closePreview() {
    if (this.elements.previewModal) {
      this.elements.previewModal.setAttribute('hidden', '');
      const moveM = this.elements.moveModal;
      if (!moveM || moveM.hasAttribute('hidden')) {
        document.body.style.overflow = '';
      }
      if (this.elements.previewFolderElement) {
        this.elements.previewFolderElement.hidden = true;
        this.elements.previewFolderElement.textContent = '';
      }
      
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
      const newAll = Array.from(checkboxes).every((cb) => cb.checked);
      const lang = getUiLang();
      const label = newAll ? t('deselect_all', lang) : t('select_all', lang);
      const icon = newAll ? '✕' : '✓';
      this.elements.selectAllBtn.innerHTML = `${icon} ${label}`;
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
        window.App.Alerts.show('No valid files to upload (allowed: images and videos up to 1 GB)', 'error');
      }
      return;
    }

    if (this.viewMode === 'by_folder' && this.folderTargetId != null) {
      formData.append('folder_id', String(this.folderTargetId));
    }

    try {
      this.setUploadButtonBusy();
      const result = await this._xhrUploadWithProgress(
        '/api/media/upload',
        formData,
        csrfToken,
        (percent) => this.setUploadButtonProgress(percent)
      );
      // Upload bytes are fully sent at this point; server may still be finalizing writes.
      this.setUploadButtonProcessing();
      // If server returned initial transcode status, show it immediately (no refresh needed).
      if (result && typeof result === 'object' && result.transcode_status && typeof result.transcode_status === 'object') {
        this.transcodeStatus = { ...(this.transcodeStatus || {}), ...(result.transcode_status || {}) };
        // Start polling if any uploaded file is queued/running.
        const anyActive = Object.values(result.transcode_status || {}).some(s => s && (s.state === 'queued' || s.state === 'running'));
        if (anyActive) {
          this.refreshTranscodeStatus({ startPolling: true });
        }
      }
      if (window.App?.Alerts?.show) {
        window.App.Alerts.show(`Uploaded ${result.files?.length || 0} file(s) successfully`, 'success');
      }
      if (this.elements.fileUploadInput) this.elements.fileUploadInput.value = '';
      await this.loadMediaFiles();
      // Only now the file is visible in gallery -> we can confidently show 100%.
      this.setUploadButtonProgress(100);
    } catch (error) {
      console.error('Upload error:', error);
      if (window.App?.Alerts?.show) {
        window.App.Alerts.show(`Upload failed: ${error.message}`, 'error');
      }
    } finally {
      this.resetUploadButton();
    }
  }

  /**
   * POST multipart with upload progress (fetch не отдаёт progress для тела запроса).
   */
  _xhrUploadWithProgress(url, formData, csrfToken, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', url);
      xhr.withCredentials = true;
      if (csrfToken) {
        xhr.setRequestHeader('X-CSRFToken', csrfToken);
      }
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && e.total > 0) {
          onProgress((e.loaded / e.total) * 100);
        } else {
          onProgress(null);
        }
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText || '{}'));
          } catch {
            resolve({});
          }
          return;
        }
        let msg = `Upload failed (${xhr.status})`;
        try {
          const err = JSON.parse(xhr.responseText || '{}');
          if (err.error) msg = err.error;
        } catch {
          /* ignore */
        }
        reject(new Error(msg));
      };
      xhr.onerror = () => reject(new Error('Network error'));
      xhr.onabort = () => reject(new Error('Upload cancelled'));
      xhr.send(formData);
    });
  }

  setUploadButtonBusy() {
    const btn = this.elements.uploadBtn;
    if (!btn) return;
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    const icon = btn.querySelector('.upload-btn__icon');
    const text = btn.querySelector('.upload-btn__text');
    const fill = btn.querySelector('.upload-btn__fill');
    if (icon) icon.textContent = '⏳';
    if (text) text.textContent = '0%';
    if (fill) {
      fill.style.width = '0%';
      fill.classList.remove('upload-btn__fill--pulse');
      fill.style.opacity = '';
    }
  }

  setUploadButtonProgress(percent) {
    const btn = this.elements.uploadBtn;
    const fill = btn?.querySelector('.upload-btn__fill');
    const text = btn?.querySelector('.upload-btn__text');
    if (!fill || !text) return;
    if (percent == null || !Number.isFinite(percent)) {
      fill.classList.add('upload-btn__fill--pulse');
      fill.style.width = '100%';
      text.textContent = t('upload_ellipsis', getUiLang());
      return;
    }
    fill.classList.remove('upload-btn__fill--pulse');
    fill.style.opacity = '';
    // XHR progress reaches 100% when bytes are sent, but server can still be saving/processing.
    // Keep at 99% until we explicitly set 100% after gallery refresh.
    const capped = percent >= 99.5 ? 99 : percent;
    const p = Math.min(100, Math.max(0, capped));
    fill.style.width = `${p}%`;
    text.textContent = `${Math.round(p)}%`;
  }

  setUploadButtonProcessing() {
    const btn = this.elements.uploadBtn;
    if (!btn) return;
    const fill = btn.querySelector('.upload-btn__fill');
    const text = btn.querySelector('.upload-btn__text');
    const icon = btn.querySelector('.upload-btn__icon');
    if (fill) {
      fill.classList.add('upload-btn__fill--pulse');
      fill.style.width = '100%';
    }
    if (text) text.textContent = t('processing_ellipsis', getUiLang());
    if (icon) icon.textContent = '⏳';
  }

  resetUploadButton() {
    const btn = this.elements.uploadBtn;
    if (!btn) return;
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
    const fill = btn.querySelector('.upload-btn__fill');
    const text = btn.querySelector('.upload-btn__text');
    const icon = btn.querySelector('.upload-btn__icon');
    if (fill) {
      fill.style.width = '0%';
      fill.classList.remove('upload-btn__fill--pulse');
      fill.style.opacity = '';
    }
    if (text) text.textContent = t('btn_upload', getUiLang());
    if (icon) icon.textContent = '⭳';
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
      this.toggleDeleteButton(false);
      this._syncMoveToolbarBtn();
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

    if (button.id === 'upload-btn') {
      if (!isLoading) this.resetUploadButton();
      return;
    }

    if (isLoading) {
      button.disabled = true;
      button.innerHTML = `⏳ ${t('deleting_ellipsis', getUiLang())}`;
    } else {
      button.disabled = false;
      button.innerHTML = `🗑 ${t('delete_selected', getUiLang())}`;
    }
  }

  initEventListeners() {
    if (this.elements.uploadBtn) {
      this.elements.uploadBtn.addEventListener('click', this.uploadMedia.bind(this));
    }

    const addLinkBtn = document.querySelector('#external-add-btn');
    const linkInput = document.querySelector('#external-url');
    if (addLinkBtn && linkInput) {
      addLinkBtn.addEventListener('click', async () => {
        const url = String(linkInput.value || '').trim();
        if (!url) {
          window.App?.Alerts?.show?.('Please paste a VK Video or Rutube link', 'warning');
          return;
        }
        addLinkBtn.disabled = true;
        try {
          const resp = await fetch('/api/media/external', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': document.querySelector('input[name="csrf_token"]')?.value
            },
            credentials: 'include',
            body: JSON.stringify({ url }),
          });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || !data?.success) {
            throw new Error(data?.error || `HTTP ${resp.status}`);
          }
          linkInput.value = '';
          window.App?.Alerts?.show?.('Link added', 'success');
          await this.loadMediaFiles();
        } catch (e) {
          window.App?.Alerts?.show?.(`Failed to add link: ${e.message}`, 'error');
        } finally {
          addLinkBtn.disabled = false;
        }
      });
    }
    
    if (this.elements.deleteSelectedBtn) {
      this.elements.deleteSelectedBtn.addEventListener('click', this.deleteSelectedMedia.bind(this));
    }

    if (this.elements.selectAllBtn) {
      this.elements.selectAllBtn.addEventListener('click', this.selectAllFiles.bind(this));
    }

    if (this.elements.searchInput) {
      this.elements.searchInput.addEventListener('input', () => {
        clearTimeout(this._searchReloadTimer);
        this._searchReloadTimer = setTimeout(() => this.loadMediaFiles(), 320);
      });
    }

    if (this.elements.sortSelect) {
      this.elements.sortSelect.addEventListener('change', () => this.loadMediaFiles());
    }

    if (this.elements.groupSelect) {
      this.elements.groupSelect.addEventListener('change', () => this.renderGallery(this.currentFiles));
    }

    if (this.elements.createFolderBtn && this.elements.newFolderNameInput) {
      this.elements.createFolderBtn.addEventListener('click', () => this._createFolderFromInput());
      this.elements.newFolderNameInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          this._createFolderFromInput();
        }
      });
    }

    this._bindViewToggleButtons();
    this._bindMoveModal();

    if (this.elements.closeModalBtn) {
      this.elements.closeModalBtn.addEventListener('click', this.closePreview.bind(this));
    }

    window.addEventListener('click', (e) => {
      const modal = this.elements.previewModal;
      if (modal && !modal.hasAttribute('hidden') && e.target === modal) {
        this.closePreview();
      }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        const moveM = this.elements.moveModal;
        if (moveM && !moveM.hasAttribute('hidden')) {
          this._closeMoveModal();
          e.preventDefault();
          return;
        }
        const modal = this.elements.previewModal;
        if (modal && !modal.hasAttribute('hidden')) {
          this.closePreview();
        }
      }
    });

    document.addEventListener('dsign:language-changed', () => {
      applyI18n();
      this._syncSelectAllLabel();
      void this._renderFolderNav();
    });

    window.addEventListener('visibilitychange', () => {
      if (!document.hidden) this.loadMediaFiles();
    });
  }

  _getCsrf() {
    return document.querySelector('input[name="csrf_token"]')?.value || '';
  }

  _syncMoveToolbarBtn() {
    const btn = this.elements.moveSelectedBtn;
    if (!btn) return;
    btn.disabled = (this.selectedFiles?.size || 0) === 0;
  }

  _bindMoveModal() {
    this.elements.moveSelectedBtn?.addEventListener('click', () => void this._openMoveModal());
    this.elements.moveModalCancel?.addEventListener('click', () => this._closeMoveModal());
    this.elements.moveModalConfirm?.addEventListener('click', () => void this._onMoveModalConfirm());
    this.elements.moveModal?.addEventListener('click', (e) => {
      if (e.target?.closest?.('[data-gallery-move-dismiss]')) {
        this._closeMoveModal();
      }
    });
  }

  _fillMoveModalSelect(folders) {
    const sel = this.elements.moveModalSelect;
    if (!sel) return;
    const lang = getUiLang();
    sel.innerHTML = '';
    const o0 = document.createElement('option');
    o0.value = '';
    o0.textContent = t('pl_filter_unsorted', lang);
    sel.appendChild(o0);
    for (const f of folders || []) {
      const o = document.createElement('option');
      o.value = String(f.id);
      o.textContent = f.name || `Folder ${f.id}`;
      sel.appendChild(o);
    }
    sel.value = '';
  }

  async _openMoveModal() {
    const lang = getUiLang();
    const n = this.selectedFiles?.size || 0;
    if (!n) {
      window.App?.Alerts?.show?.(t('gallery_bulk_move_none', lang), 'warning');
      return;
    }
    if (this.elements.previewModal && !this.elements.previewModal.hasAttribute('hidden')) {
      this.closePreview();
    }
    let folders = [];
    try {
      const res = await fetch('/api/media/folders', {
        headers: { Accept: 'application/json' },
        credentials: 'include',
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data?.success) {
        folders = data.folders || [];
        this._foldersCache = folders;
      }
    } catch (e) {
      console.error('Failed to load folders for move modal', e);
    }
    this._fillMoveModalSelect(folders);
    const m = this.elements.moveModal;
    if (m) {
      m.removeAttribute('hidden');
      document.body.style.overflow = 'hidden';
    }
    queueMicrotask(() => this.elements.moveModalSelect?.focus());
  }

  _closeMoveModal() {
    const m = this.elements.moveModal;
    if (m) m.setAttribute('hidden', '');
    const pv = this.elements.previewModal;
    if (pv && !pv.hasAttribute('hidden')) return;
    document.body.style.overflow = '';
  }

  async _onMoveModalConfirm() {
    const keys = Array.from(this.selectedFiles || []);
    const lang = getUiLang();
    if (!keys.length) {
      this._closeMoveModal();
      return;
    }
    const raw = this.elements.moveModalSelect?.value;
    const folderId = raw === '' || raw == null ? null : Number(raw);
    if (folderId != null && !Number.isFinite(folderId)) {
      window.App?.Alerts?.show?.(t('gallery_bulk_move_err', lang), 'error');
      return;
    }
    const items = keys.map((storage_key) => ({ storage_key, folder_id: folderId }));
    const csrf = this._getCsrf();
    try {
      const res = await fetch('/api/media/item-meta/batch', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          ...(csrf ? { 'X-CSRFToken': csrf } : {}),
        },
        credentials: 'include',
        body: JSON.stringify({ items }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data?.success) {
        throw new Error(data?.error || `HTTP ${res.status}`);
      }
      window.App?.Alerts?.show?.(
        t('gallery_bulk_moved', lang).replace('{n}', String(data.updated ?? keys.length)),
        'success'
      );
      this.selectedFiles.clear();
      document.querySelectorAll('.file-checkbox').forEach((cb) => {
        cb.checked = false;
      });
      this._syncMoveToolbarBtn();
      this.toggleDeleteButton(false);
      this._closeMoveModal();
      await this._renderFolderNav();
      await this.loadMediaFiles();
    } catch (e) {
      window.App?.Alerts?.show?.(`${t('gallery_bulk_move_err', lang)}: ${e.message}`, 'error');
    }
  }

  _syncViewToggleButtons() {
    const a = this.elements.viewAllBtn;
    const b = this.elements.viewByFolderBtn;
    if (a) a.classList.toggle('is-active', this.viewMode === 'all');
    if (b) b.classList.toggle('is-active', this.viewMode === 'by_folder');
  }

  _syncFolderColumnState() {
    const col = this.elements.folderColumn;
    if (col) col.classList.toggle('gallery-folder-column--inactive', this.viewMode === 'all');
  }

  _bindViewToggleButtons() {
    const setMode = (mode) => {
      if (this.viewMode === mode) return;
      const prev = this.viewMode;
      this.viewMode = mode;
      if (mode === 'all') {
        this.folderTargetId = null;
      } else if (mode === 'by_folder' && prev === 'all') {
        this.folderTargetId = null;
      }
      this._syncFolderColumnState();
      this._syncViewToggleButtons();
      void this._renderFolderNav().then(() => this.loadMediaFiles());
    };
    this.elements.viewAllBtn?.addEventListener('click', () => setMode('all'));
    this.elements.viewByFolderBtn?.addEventListener('click', () => setMode('by_folder'));
  }

  async _renderFolderNav() {
    const nav = this.elements.folderNav;
    if (!nav) return;
    nav.innerHTML = '';
    const lang = getUiLang();

    const unsRow = document.createElement('div');
    unsRow.className = 'gallery-folder-row gallery-folder-row--solo';
    const unsInner = document.createElement('div');
    unsInner.className = 'gallery-folder-row__inner';
    const uns = document.createElement('button');
    uns.type = 'button';
    uns.className =
      'gallery-folder-nav__btn' + (this.folderTargetId === null ? ' is-active' : '');
    uns.dataset.i18n = 'pl_filter_unsorted';
    uns.textContent = t('pl_filter_unsorted', lang);
    uns.addEventListener('click', () => {
      this.folderTargetId = null;
      void this._renderFolderNav().then(() => this.loadMediaFiles());
    });
    unsInner.appendChild(uns);
    unsRow.appendChild(unsInner);
    nav.appendChild(unsRow);

    let folders = [];
    try {
      const res = await fetch('/api/media/folders', {
        headers: { Accept: 'application/json' },
        credentials: 'include',
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data?.success) {
        folders = data.folders || [];
      }
    } catch (e) {
      console.error('Failed to load folders', e);
    }

    this._foldersCache = folders;

    for (const f of folders) {
      const row = document.createElement('div');
      row.className = 'gallery-folder-row';
      const inner = document.createElement('div');
      inner.className = 'gallery-folder-row__inner';
      const b = document.createElement('button');
      b.type = 'button';
      b.className =
        'gallery-folder-nav__btn gallery-folder-nav__btn--row' +
        (this.folderTargetId === f.id ? ' is-active' : '');
      b.textContent = f.name || `Folder ${f.id}`;
      const fid = f.id;
      b.addEventListener('click', () => {
        this.folderTargetId = fid;
        void this._renderFolderNav().then(() => this.loadMediaFiles());
      });

      const icons = document.createElement('div');
      icons.className = 'gallery-folder-row__icons';
      const renameBtn = document.createElement('button');
      renameBtn.type = 'button';
      renameBtn.className = 'gallery-folder-row__icon-btn';
      renameBtn.title = t('gallery_folder_rename', lang);
      renameBtn.setAttribute('aria-label', t('gallery_folder_rename', lang));
      renameBtn.textContent = '✎';
      renameBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        void this._renameFolder(fid, f.name);
      });

      const delBtn = document.createElement('button');
      delBtn.type = 'button';
      delBtn.className = 'gallery-folder-row__icon-btn';
      delBtn.title = t('gallery_folder_delete', lang);
      delBtn.setAttribute('aria-label', t('gallery_folder_delete', lang));
      delBtn.textContent = '🗑';
      delBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        void this._deleteFolder(fid, f.name);
      });

      icons.appendChild(renameBtn);
      icons.appendChild(delBtn);
      inner.appendChild(b);
      inner.appendChild(icons);
      row.appendChild(inner);
      nav.appendChild(row);
    }
  }

  async _renameFolder(folderId, currentName) {
    const lang = getUiLang();
    const next = window.prompt(t('gallery_folder_rename_prompt', lang), currentName || '');
    if (next == null) return;
    const name = String(next).trim();
    if (!name || name === currentName) return;
    const csrf = this._getCsrf();
    try {
      const res = await fetch(`/api/media/folders/${folderId}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          ...(csrf ? { 'X-CSRFToken': csrf } : {}),
        },
        credentials: 'include',
        body: JSON.stringify({ name }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data?.success) {
        throw new Error(data?.error || `HTTP ${res.status}`);
      }
      window.App?.Alerts?.show?.(t('gallery_folder_renamed', lang), 'success');
      await this._renderFolderNav();
      await this.loadMediaFiles();
    } catch (e) {
      window.App?.Alerts?.show?.(`${t('gallery_folder_rename_err', lang)}: ${e.message}`, 'error');
    }
  }

  async _deleteFolder(folderId, folderName) {
    const lang = getUiLang();
    const label = folderName || String(folderId);
    if (!window.confirm(t('gallery_folder_delete_confirm', lang).replace('{name}', label))) {
      return;
    }
    const csrf = this._getCsrf();
    try {
      const res = await fetch(`/api/media/folders/${folderId}`, {
        method: 'DELETE',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          ...(csrf ? { 'X-CSRFToken': csrf } : {}),
        },
        credentials: 'include',
        body: JSON.stringify({ mode: 'metadata' }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data?.success) {
        throw new Error(data?.error || `HTTP ${res.status}`);
      }
      if (this.folderTargetId === folderId) {
        this.folderTargetId = null;
      }
      window.App?.Alerts?.show?.(t('gallery_folder_deleted', lang), 'success');
      await this._renderFolderNav();
      await this.loadMediaFiles();
    } catch (e) {
      window.App?.Alerts?.show?.(`${t('gallery_folder_delete_err', lang)}: ${e.message}`, 'error');
    }
  }

  async _createFolderFromInput() {
    const inp = this.elements.newFolderNameInput;
    const name = String(inp?.value || '').trim();
    const lang = getUiLang();
    if (!name) {
      window.App?.Alerts?.show?.(t('gallery_new_folder_ph', lang), 'warning');
      return;
    }
    const csrf = this._getCsrf();
    try {
      const res = await fetch('/api/media/folders', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          ...(csrf ? { 'X-CSRFToken': csrf } : {}),
        },
        credentials: 'include',
        body: JSON.stringify({ name }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data?.success) {
        throw new Error(data?.error || `HTTP ${res.status}`);
      }
      if (inp) inp.value = '';
      window.App?.Alerts?.show?.(t('gallery_folder_created', lang), 'success');
      const newId = data.folder?.id;
      if (this.viewMode === 'by_folder' && newId != null) {
        this.folderTargetId = newId;
      }
      await this._renderFolderNav();
      await this.loadMediaFiles();
    } catch (e) {
      window.App?.Alerts?.show?.(`${t('gallery_folder_create_err', lang)}: ${e.message}`, 'error');
    }
  }

  _syncSelectAllLabel() {
    if (!this.elements.selectAllBtn) return;
    const checkboxes = document.querySelectorAll('.file-checkbox');
    const lang = getUiLang();
    if (!checkboxes.length) {
      this.elements.selectAllBtn.textContent = `☑ ${t('select_all', lang)}`;
      return;
    }
    const allSelected = Array.from(checkboxes).every((cb) => cb.checked);
    const label = allSelected ? t('deselect_all', lang) : t('select_all', lang);
    this.elements.selectAllBtn.textContent = `${allSelected ? '✖' : '☑'} ${label}`;
  }
}

// Initialize the gallery
function initializeGallery() {
  try {
    if (window.App?.MediaGallery) {
      return window.App.MediaGallery;
    }
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
