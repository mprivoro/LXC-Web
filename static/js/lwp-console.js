/* LXC attach console (xterm.js + WebSocket).
 * Full-page window/tab: #consoleTerm[data-container-name]
 * Home Overview: .console-open opens that page in a popup (Ctrl/middle-click = tab).
 */
(function ($) {
	var term = null;
	var fitAddon = null;
	var socket = null;

	function closeConsole() {
		if (socket) {
			try { socket.close(); } catch (e) {}
			socket = null;
		}
		if (term) {
			try { term.dispose(); } catch (e) {}
			term = null;
			fitAddon = null;
			$('#consoleTerm').empty();
		}
	}

	function sendResize() {
		if (!term || !socket || socket.readyState !== 1) {
			return;
		}
		socket.send('\x1fR' + term.cols + 'x' + term.rows);
	}

	function sendRaw(data) {
		if (socket && socket.readyState === 1 && data) {
			socket.send(data);
		}
	}

	function focusTerm() {
		if (term) {
			term.focus();
		}
	}

	function isXtermTarget(el) {
		if (!el) {
			return false;
		}
		if (el.tagName === 'TEXTAREA') {
			return true;
		}
		if (el.classList && (
			el.classList.contains('xterm') ||
			el.classList.contains('xterm-helper-textarea') ||
			el.classList.contains('xterm-screen')
		)) {
			return true;
		}
		return !!(el.closest && el.closest('#consoleTerm, .xterm'));
	}

	function shellKeySequence(e) {
		var ctrl = e.ctrlKey || e.metaKey;
		var alt = e.altKey;
		var code = e.code || '';
		if (ctrl && !alt && !e.shiftKey && code === 'KeyW') {
			return '\x17';
		}
		if (ctrl && !alt && !e.shiftKey && code === 'KeyK') {
			return '\x0b';
		}
		if (ctrl && !alt && !e.shiftKey && code === 'KeyR') {
			return '\x12';
		}
		if (alt && !ctrl && code === 'KeyW') {
			return '\x1bw';
		}
		return '';
	}

	function copySelection() {
		if (!term || typeof term.getSelection !== 'function') {
			return false;
		}
		var sel = term.getSelection();
		if (!sel) {
			return false;
		}
		if (navigator.clipboard && navigator.clipboard.writeText) {
			navigator.clipboard.writeText(sel).catch(function () {});
			return true;
		}
		try {
			var ta = document.createElement('textarea');
			ta.value = sel;
			document.body.appendChild(ta);
			ta.select();
			document.execCommand('copy');
			document.body.removeChild(ta);
			return true;
		} catch (err) {
			return false;
		}
	}

	function pasteText(text) {
		if (!text) {
			return;
		}
		if (term && typeof term.paste === 'function') {
			term.paste(text);
			return;
		}
		sendRaw(text);
	}

	function trapConsoleInput() {
		window.addEventListener('keydown', function (e) {
			var ctrl = e.ctrlKey || e.metaKey;
			var seq = shellKeySequence(e);
			if (seq) {
				e.preventDefault();
				e.stopPropagation();
				if (e.stopImmediatePropagation) {
					e.stopImmediatePropagation();
				}
				sendRaw(seq);
				focusTerm();
				return;
			}
			if (ctrl && e.shiftKey && e.code === 'KeyC') {
				e.preventDefault();
				copySelection();
				return;
			}
			if (ctrl && !e.shiftKey && e.code === 'KeyC' && term && term.getSelection()) {
				e.preventDefault();
				copySelection();
				return;
			}
			if (e.shiftKey && e.code === 'Insert' && !ctrl) {
				e.preventDefault();
				if (navigator.clipboard && navigator.clipboard.readText) {
					navigator.clipboard.readText().then(pasteText).catch(function () {});
				}
				return;
			}
			if (isXtermTarget(e.target)) {
				return;
			}
			if (e.key === ' ' && !ctrl && !e.altKey) {
				e.preventDefault();
				e.stopPropagation();
				sendRaw(' ');
				focusTerm();
			}
		}, true);

		document.addEventListener('paste', function (e) {
			var text = e.clipboardData && e.clipboardData.getData('text/plain');
			if (!text) {
				return;
			}
			e.preventDefault();
			pasteText(text);
			focusTerm();
		}, true);

		document.addEventListener('copy', function (e) {
			if (!term || typeof term.getSelection !== 'function') {
				return;
			}
			var sel = term.getSelection();
			if (!sel) {
				return;
			}
			if (e.clipboardData) {
				e.clipboardData.setData('text/plain', sel);
				e.preventDefault();
			}
		}, true);
	}

	function openConsole(name) {
		closeConsole();
		term = new Terminal({
			cursorBlink: true,
			fontSize: 13,
			fontFamily: 'Menlo, Consolas, "Courier New", monospace',
			theme: { background: '#000000', foreground: '#dddddd' },
			rightClickSelectsWord: false
		});
		fitAddon = new FitAddon.FitAddon();
		term.loadAddon(fitAddon);
		term.open(document.getElementById('consoleTerm'));
		try { fitAddon.fit(); } catch (e) {}

		var proto = (location.protocol === 'https:') ? 'wss://' : 'ws://';
		var url = proto + location.host + $SCRIPT_ROOT + '/console/' + encodeURIComponent(name);
		socket = new WebSocket(url);
		socket.onmessage = function (ev) {
			if (term) {
				term.write(ev.data);
			}
		};
		socket.onerror = function () {
			if (term) {
				term.write('\r\n[console error]\r\n');
			}
		};
		socket.onclose = function () {
			if (term) {
				term.write('\r\n[disconnected]\r\n');
			}
		};
		socket.onopen = function () {
			sendResize();
			focusTerm();
		};
		term.onData(function (data) {
			sendRaw(data);
		});
		term.onResize(function () {
			sendResize();
		});
		setTimeout(function () {
			try { fitAddon.fit(); } catch (e) {}
			focusTerm();
		}, 80);
	}

	function popupFeatures() {
		var width = Math.max(720, Math.min(1100, window.screen.availWidth - 80));
		var height = Math.max(420, Math.min(720, window.screen.availHeight - 80));
		return 'toolbar=no,location=no,menubar=no,status=no,scrollbars=no,resizable=yes,width='
			+ width + ',height=' + height;
	}

	$(function () {
		var pageName = $('#consoleTerm').data('container-name');
		if (pageName) {
			trapConsoleInput();
			openConsole(pageName);
			$('.console-send-key').on('mousedown', function (e) {
				e.preventDefault();
			});
			$('.console-send-key').on('click', function (e) {
				e.preventDefault();
				var keys = {
					'ctrl-w': '\x17',
					'ctrl-k': '\x0b',
					'ctrl-r': '\x12',
					'alt-w': '\x1bw'
				};
				sendRaw(keys[$(this).data('key')] || '');
				focusTerm();
			});
			$(document).on('mousedown', '#consoleTerm', function () {
				focusTerm();
			});
			$(window).on('pagehide beforeunload', closeConsole);
			$(window).on('resize', function () {
				if (fitAddon) {
					try { fitAddon.fit(); } catch (e) {}
				}
			});
			return;
		}

		$('.console-open').on('click', function (e) {
			if (e.ctrlKey || e.metaKey || e.shiftKey || e.which === 2) {
				return;
			}
			e.preventDefault();
			var name = $(this).data('container-name') || '';
			var href = this.href;
			var w = window.open(href, 'lwp-console-' + name, popupFeatures());
			if (w) {
				w.focus();
			} else {
				window.open(href, '_blank');
			}
		});
	});
})(jQuery);
