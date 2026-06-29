/** Shared gallery / modal tile helpers for local audio files. */

export const AUDIO_EXTENSIONS = ['mp3', 'wav', 'ogg', 'oga', 'flac', 'm4a', 'aac', 'opus'];

export function isAudioMedia(file) {
  if (file?.is_audio) return true;
  const name = String(file?.name || file?.filename || '');
  const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : '';
  return AUDIO_EXTENSIONS.includes(ext);
}

export function audioFormatLabel(filename) {
  const ext = String(filename || '').split('.').pop().toLowerCase();
  if (!ext) return 'AUDIO';
  if (ext === 'oga') return 'OGG';
  return ext.toUpperCase();
}

export function createAudioNotePreview({ className = '' } = {}) {
  const previewContainer = document.createElement('div');
  previewContainer.className = [
    'file-preview-container',
    'file-preview-container--audio',
    'file-icon',
    className,
  ]
    .filter(Boolean)
    .join(' ');
  const icon = document.createElement('span');
  icon.className = 'file-icon__glyph file-icon__glyph--audio';
  icon.setAttribute('aria-hidden', 'true');
  icon.textContent = '♪';
  previewContainer.appendChild(icon);
  return previewContainer;
}

export function appendFormatBadge(parent, label, variant = 'audio') {
  const badge = document.createElement('div');
  badge.className = `provider-badge provider-badge--${variant} provider-badge--format`;
  badge.textContent = label;
  parent.appendChild(badge);
  return badge;
}
