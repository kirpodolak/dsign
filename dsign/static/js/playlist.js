import { fetchAPI } from './utils/api.js';
import { showAlert } from './utils/alerts.js';
import { toggleButtonState } from './utils/helpers.js';

const playlistId = new URLSearchParams(window.location.search).get('id');
const fileListEl = document.getElementById('file-list');
const saveBtn = document.getElementById('save-playlist');

async function loadMediaFiles() {
  try {
    const files = await fetchAPI(`/api/media/files?playlist_id=${playlistId}`);
    renderFileTable(files);
  } catch (error) {
    showAlert('Failed to load media files', 'error');
  }
}

function renderFileTable(files) {
  fileListEl.innerHTML = '';
  files.forEach((file, index) => {
    const row = document.createElement('tr');
    row.innerHTML = `
      <td>${index + 1}</td>
      <td><input type="checkbox" class="include-checkbox" data-id="${file.id}" ${file.included ? 'checked' : ''}></td>
      <td><img src="/media/${file.filename}" alt="Preview" style="height: 40px"></td>
      <td>${file.filename}</td>
      <td>
        <input type="number" class="duration-input" data-id="${file.id}" value="${file.duration}" min="1" ${file.is_video ? 'readonly' : ''}>
      </td>
    `;
    fileListEl.appendChild(row);
  });
}

async function savePlaylist() {
  toggleButtonState(saveBtn, true);
  const includedFiles = [...document.querySelectorAll('.include-checkbox')].filter(cb => cb.checked).map(cb => cb.dataset.id);
  const durations = {};
  [...document.querySelectorAll('.duration-input')].forEach(input => {
    durations[input.dataset.id] = parseInt(input.value, 10);
  });

  try {
    await fetchAPI(`/api/playlists/${playlistId}/files`, {
      method: 'POST',
      body: JSON.stringify({ included_files: includedFiles, durations }),
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCSRFToken()
      }
    });
    showAlert('Playlist saved successfully', 'success');
  } catch (error) {
    showAlert('Failed to save playlist', 'error');
  } finally {
    toggleButtonState(saveBtn, false);
  }
}

function getCSRFToken() {
  return document.querySelector('input[name="csrf_token"]').value;
}

saveBtn.addEventListener('click', savePlaylist);

window.addEventListener('DOMContentLoaded', loadMediaFiles);
