/**
 * Modal: pick warehouse media to append to the current playlist (cart + filters).
 */
import { t, getUiLang, applyI18n } from './i18n.js';
import {
  isAudioMedia,
  audioFormatLabel,
  createAudioNotePreview,
  appendFormatBadge,
} from './media-tile-ui.js';

function thumbUrl(filename) {
  return `/api/media/thumbnail/${encodeURIComponent(filename)}`;
}

function isAudioFile(file) {
  return isAudioMedia(file);
}

function isVideoFile(file) {
  const fn = String(file?.filename || '');
  if (file?.is_video || file?.is_external || fn.toLowerCase().startsWith('ext-')) return true;
  return ['.mp4', '.avi', '.mov', '.mkv', '.webm', '.m4v'].some((ext) => fn.toLowerCase().endsWith(ext));
}

export class AddToPlaylistModal {
  /**
   * @param {string|number} playlistId
   * @param {{ getCSRFToken: () => string, onAppended: () => void, showMessage?: (msg: string, type?: string) => void }} hooks
   */
  constructor(playlistId, hooks) {
    this.playlistId = playlistId;
    this.getCSRFToken = hooks.getCSRFToken;
    this.onAppended = hooks.onAppended;
    this.showMessage = hooks.showMessage || ((msg) => window.alert(msg));
    this._root = null;
    this._playlistKeys = new Set();
    this._folders = [];
    this._navMode = 'all'; // 'all' | 'unsorted' | 'folder'
    this._folderId = null;
    this._search = '';
    this._sort = 'name-asc';
    this._cart = [];
    this._cartSet = new Set();
    /** @type {string} */
    this._foldersNavSig = '';
    this._navDelegated = false;
    this._files = [];
    this._searchTimer = null;
    this._boundDocKeydown = (e) => {
      if (e.key === 'Escape' && this.isOpen()) this.close();
    };
  }

  isOpen() {
    return this._root && !this._root.hidden;
  }

  _ensureDom() {
    if (this._root) return;
    const wrap = document.createElement('div');
    wrap.id = 'add-to-playlist-modal';
    wrap.className = 'pl-add-modal';
    wrap.hidden = true;
    wrap.setAttribute('aria-hidden', 'true');
    wrap.innerHTML = `
      <div class="pl-add-modal__backdrop" data-act="close"></div>
      <div class="pl-add-modal__dialog" role="dialog" aria-modal="true" aria-labelledby="pl-add-modal-title">
        <header class="pl-add-modal__head">
          <h2 id="pl-add-modal-title" class="pl-add-modal__title" data-i18n="pl_modal_title">Добавить медиа в плейлист</h2>
          <button type="button" class="pl-add-modal__close" data-act="close" aria-label="×">×</button>
        </header>
        <div class="pl-add-modal__body">
          <aside class="pl-add-modal__side">
            <div class="pl-add-modal__side-title" data-i18n="pl_modal_filter">Папка</div>
            <div class="pl-add-modal__nav" id="pl-add-folder-nav"></div>
            <label class="pl-add-modal__label" for="pl-add-search" data-i18n="gallery_search_sort">Поиск</label>
            <input type="search" id="pl-add-search" class="pl-add-modal__input" data-i18n-placeholder="search_placeholder" autocomplete="off" />
            <label class="pl-add-modal__label" for="pl-add-sort" data-i18n="sort_type">Сортировка</label>
            <select id="pl-add-sort" class="pl-add-modal__select">
              <option value="name-asc" data-i18n="sort_name_az">Имя А–Я</option>
              <option value="name-desc" data-i18n="sort_name_za">Имя Я–А</option>
              <option value="date-newest" data-i18n="sort_date_new">Дата новые</option>
              <option value="date-oldest" data-i18n="sort_date_old">Дата старые</option>
              <option value="type" data-i18n="sort_type">Тип</option>
            </select>
          </aside>
          <section class="pl-add-modal__center" aria-live="polite">
            <div id="pl-add-grid" class="pl-add-modal__grid"></div>
            <p id="pl-add-grid-empty" class="pl-add-modal__empty" hidden data-i18n="pl_modal_no_files">Нет файлов</p>
          </section>
          <aside class="pl-add-modal__cart-panel">
            <div class="pl-add-modal__side-title" data-i18n="pl_modal_cart">Корзина</div>
            <div class="pl-add-modal__cart-count"><span id="pl-add-cart-n">0</span> <span data-i18n="pl_modal_cart_items">позиций</span></div>
            <ul id="pl-add-cart-list" class="pl-add-modal__cart-list"></ul>
            <button type="button" id="pl-add-submit" class="btn primary pl-add-modal__submit" data-i18n="pl_modal_add_to_pl">Добавить в плейлист</button>
          </aside>
        </div>
      </div>
    `;
    document.body.appendChild(wrap);
    this._root = wrap;

    wrap.addEventListener('click', (e) => {
      const t = e.target;
      if (t && t.getAttribute && t.getAttribute('data-act') === 'close') this.close();
    });
    wrap.querySelector('#pl-add-search').addEventListener('input', () => {
      this._search = wrap.querySelector('#pl-add-search').value || '';
      clearTimeout(this._searchTimer);
      this._searchTimer = setTimeout(() => this._reloadGrid(), 280);
    });
    wrap.querySelector('#pl-add-sort').addEventListener('change', () => {
      this._sort = wrap.querySelector('#pl-add-sort').value || 'name-asc';
      this._reloadGrid();
    });
    wrap.querySelector('#pl-add-submit').addEventListener('click', () => this._submitAppend());
    this._initNavDelegation();
  }

  _initNavDelegation() {
    const nav = this._root?.querySelector('#pl-add-folder-nav');
    if (!nav || this._navDelegated) return;
    this._navDelegated = true;
    nav.addEventListener('click', async (e) => {
      const b = e.target.closest('.pl-add-modal__nav-btn');
      if (!b || !nav.contains(b)) return;
      const mode = b.dataset.mode;
      if (!mode) return;
      const fidRaw = b.dataset.folderId;
      this._navMode = mode;
      this._folderId = fidRaw != null && fidRaw !== '' ? Number(fidRaw) : null;
      this._updatePlAddNavActive();
      await this._reloadGrid();
    });
  }

  async open() {
    this._ensureDom();
    this._cart = [];
    this._cartSet.clear();
    this._search = '';
    this._sort = 'name-asc';
    this._navMode = 'all';
    this._folderId = null;
    this._foldersNavSig = '';

    await this._loadPlaylistKeys();
    await this._loadFolders();
    await this._reloadNav(true);
    const inp = this._root.querySelector('#pl-add-search');
    if (inp) inp.value = this._search;
    const sortSel = this._root.querySelector('#pl-add-sort');
    if (sortSel) sortSel.value = this._sort;
    await this._reloadGrid();
    this._renderCart();

    this._root.hidden = false;
    this._root.setAttribute('aria-hidden', 'false');
    document.addEventListener('keydown', this._boundDocKeydown);
    document.body.classList.add('pl-add-modal--open');
    applyI18n(this._root);
  }

  close() {
    if (!this._root) return;
    this._root.hidden = true;
    this._root.setAttribute('aria-hidden', 'true');
    document.removeEventListener('keydown', this._boundDocKeydown);
    document.body.classList.remove('pl-add-modal--open');
  }

  async _loadPlaylistKeys() {
    this._playlistKeys = new Set();
    const res = await fetch(`/api/playlists/${this.playlistId}/items`, {
      headers: { Accept: 'application/json' },
      credentials: 'include',
    });
    if (!res.ok) return;
    const data = await res.json();
    for (const it of data.items || []) {
      if (it?.file_name) this._playlistKeys.add(it.file_name);
    }
  }

  async _loadFolders() {
    this._folders = [];
    const res = await fetch('/api/media/folders', {
      headers: { Accept: 'application/json' },
      credentials: 'include',
    });
    if (!res.ok) return;
    const data = await res.json();
    if (!data?.success) return;
    this._folders = data.folders || [];
  }

  _foldersNavSignature() {
    const list = (this._folders || [])
      .map((f) => [Number(f.id), String(f.name || '')])
      .sort((a, b) => a[0] - b[0]);
    return JSON.stringify(list);
  }

  _updatePlAddNavActive() {
    const nav = this._root.querySelector('#pl-add-folder-nav');
    if (!nav) return;
    nav.querySelectorAll('.pl-add-modal__nav-btn').forEach((b) => {
      const mode = b.dataset.mode;
      const fidRaw = b.dataset.folderId;
      const fid = fidRaw != null && fidRaw !== '' ? Number(fidRaw) : null;
      let active = false;
      if (mode === 'all') active = this._navMode === 'all';
      else if (mode === 'unsorted') active = this._navMode === 'unsorted';
      else if (mode === 'folder') active = this._navMode === 'folder' && Number(fid) === Number(this._folderId);
      b.classList.toggle('is-active', active);
    });
  }

  async _reloadNav(forceFull = false) {
    const nav = this._root.querySelector('#pl-add-folder-nav');
    if (!nav) return;
    const sig = this._foldersNavSignature();
    const unchanged = !forceFull && sig === this._foldersNavSig && nav.childElementCount > 0;
    if (unchanged) {
      this._updatePlAddNavActive();
      applyI18n(nav);
      return;
    }
    this._foldersNavSig = sig;
    nav.innerHTML = '';
    const mkBtn = (labelKey, mode, folderId, labelText) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'pl-add-modal__nav-btn';
      b.dataset.mode = mode;
      if (folderId != null) b.dataset.folderId = String(folderId);
      if (labelKey) b.setAttribute('data-i18n', labelKey);
      if (labelText) b.textContent = labelText;
      nav.appendChild(b);
    };
    mkBtn('pl_filter_all', 'all', null);
    mkBtn('pl_filter_unsorted', 'unsorted', null);
    for (const f of this._folders) {
      mkBtn(null, 'folder', f.id, f.name || `Folder ${f.id}`);
    }
    this._updatePlAddNavActive();
    applyI18n(nav);
  }

  _mediaUrl() {
    const p = new URLSearchParams();
    if (this._navMode === 'all') {
      p.set('view', 'all');
    } else if (this._navMode === 'unsorted') {
      p.set('view', 'by_folder');
    } else {
      p.set('view', 'by_folder');
      p.set('folder_id', String(this._folderId));
    }
    if (this._search.trim()) p.set('search', this._search.trim());
    p.set('sort', this._sort || 'name-asc');
    return `/api/media/files?${p.toString()}`;
  }

  async _reloadGrid() {
    const grid = this._root.querySelector('#pl-add-grid');
    const empty = this._root.querySelector('#pl-add-grid-empty');
    if (!grid) return;
    grid.innerHTML = '';
    const lang = getUiLang();
    const idle = 'idle_logo.jpg';
    try {
      const res = await fetch(this._mediaUrl(), {
        headers: { Accept: 'application/json' },
        credentials: 'include',
      });
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      if (!data.success) throw new Error(data.error || 'load failed');
      this._files = (data.files || []).filter((f) => String(f.filename || '').toLowerCase() !== idle);
    } catch (e) {
      this._files = [];
      console.error(e);
    }

    if (!this._files.length) {
      empty.hidden = false;
      applyI18n(empty);
      return;
    }
    empty.hidden = true;

    for (const file of this._files) {
      const fn = file.filename;
      const inPl = this._playlistKeys.has(fn);
      const inCart = this._cartSet.has(fn);
      const vid = isVideoFile(file);
      const aud = isAudioFile(file);

      const tile = document.createElement('div');
      tile.className = 'pl-add-tile';
      if (inPl) tile.classList.add('pl-add-tile--in-playlist');
      if (inCart) tile.classList.add('pl-add-tile--in-cart');
      if (aud) tile.classList.add('pl-add-tile--audio');

      let previewEl;
      if (aud) {
        previewEl = createAudioNotePreview({ className: 'pl-add-tile__preview' });
      } else {
        const img = document.createElement('img');
        img.className = 'pl-add-tile__img';
        img.alt = '';
        img.loading = 'lazy';
        img.src = vid ? thumbUrl(fn) : `/api/media/${encodeURIComponent(fn)}`;
        img.onerror = () => {
          img.src = '/static/images/placeholder.jpg';
        };
        previewEl = img;
      }

      if (aud) {
        appendFormatBadge(tile, audioFormatLabel(fn), 'audio');
      }

      const meta = document.createElement('div');
      meta.className = 'pl-add-tile__meta';

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'pl-add-tile__cb';
      cb.dataset.filename = fn;
      if (inPl) {
        cb.disabled = true;
        cb.checked = false;
      } else if (inCart) {
        cb.disabled = true;
        cb.checked = true;
      } else {
        cb.checked = false;
        cb.addEventListener('change', async () => {
          if (cb.checked) {
            this._addToCart(fn);
            this._renderCart();
            await this._reloadGrid();
          }
        });
      }

      const name = document.createElement('div');
      name.className = 'pl-add-tile__name';
      name.textContent = fn;

      const badge = document.createElement('div');
      badge.className = 'pl-add-tile__badge';
      if (inPl) badge.textContent = t('pl_already_in_pl', lang);
      else if (inCart) badge.textContent = t('pl_in_cart', lang);

      meta.appendChild(cb);
      meta.appendChild(name);
      if (inPl || inCart) meta.appendChild(badge);

      tile.appendChild(previewEl);
      tile.appendChild(meta);
      grid.appendChild(tile);
    }
  }

  _addToCart(fn) {
    if (this._playlistKeys.has(fn) || this._cartSet.has(fn)) return;
    this._cart.push(fn);
    this._cartSet.add(fn);
  }

  _removeFromCart(fn) {
    this._cart = this._cart.filter((k) => k !== fn);
    this._cartSet.delete(fn);
  }

  _renderCart() {
    const list = this._root.querySelector('#pl-add-cart-list');
    const nEl = this._root.querySelector('#pl-add-cart-n');
    if (!list || !nEl) return;
    list.innerHTML = '';
    nEl.textContent = String(this._cart.length);
    const lang = getUiLang();
    for (const key of this._cart) {
      const li = document.createElement('li');
      li.className = 'pl-add-cart__item';
      const span = document.createElement('span');
      span.className = 'pl-add-cart__name';
      span.textContent = key;
      const rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'pl-add-cart__rm';
      rm.setAttribute('aria-label', t('pl_cart_remove', lang));
      rm.textContent = '×';
      rm.addEventListener('click', () => {
        this._removeFromCart(key);
        this._renderCart();
        this._reloadGrid();
      });
      li.appendChild(span);
      li.appendChild(rm);
      list.appendChild(li);
    }
  }

  async _submitAppend() {
    if (!this._cart.length) {
      const lang = getUiLang();
      this.showMessage(t('pl_cart_empty', lang), 'warning');
      return;
    }
    const btn = this._root.querySelector('#pl-add-submit');
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(`/api/playlists/${this.playlistId}/files/append`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          'X-CSRFToken': this.getCSRFToken(),
        },
        credentials: 'include',
        body: JSON.stringify({ items: [...this._cart] }),
      });
      const out = await res.json().catch(() => ({}));
      if (!res.ok || !out.success) {
        throw new Error(out.error || `HTTP ${res.status}`);
      }
      const lang = getUiLang();
      const added = (out.added && out.added.length) || 0;
      const skipped = out.skipped && out.skipped.length ? ` (${out.skipped.length} ${t('pl_append_skipped', lang)})` : '';
      this.showMessage(`${t('pl_append_ok', lang)}: ${added}${skipped}`, 'success');
      for (const k of out.added || []) this._playlistKeys.add(k);
      this._cart = [];
      this._cartSet.clear();
      this._renderCart();
      await this._reloadGrid();
      this.onAppended();
      this.close();
    } catch (e) {
      console.error(e);
      this.showMessage(String(e.message || e), 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  }
}
