// callsight docs — theme persistence, copy buttons, tabs, TOC highlighting.

(function () {
    'use strict';

    // ---- Theme ----
    var THEME_KEY = 'callsight-docs-theme';
    function setTheme(t) {
        document.documentElement.setAttribute('data-theme', t);
        try { localStorage.setItem(THEME_KEY, t); } catch (e) {}
        var btn = document.getElementById('theme-toggle');
        if (btn) btn.setAttribute('aria-label', 'Toggle theme (current: ' + t + ')');
    }
    var saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (e) {}
    if (saved === 'light' || saved === 'dark') {
        setTheme(saved);
    } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
        setTheme('light');
    }
    document.addEventListener('click', function (e) {
        if (!e.target.closest('#theme-toggle')) return;
        var cur = document.documentElement.getAttribute('data-theme') || 'dark';
        setTheme(cur === 'dark' ? 'light' : 'dark');
    });

    // ---- Copy buttons on every code block ----
    function addCopyButtons() {
        document.querySelectorAll('pre').forEach(function (pre) {
            if (pre.querySelector('.copy-btn')) return;
            var btn = document.createElement('button');
            btn.className = 'copy-btn';
            btn.type = 'button';
            btn.textContent = 'Copy';
            btn.addEventListener('click', function () {
                var code = pre.querySelector('code') || pre;
                // Drop leading shell prompts so the copy pastes cleanly.
                var text = code.innerText.replace(/^\$ /gm, '');
                navigator.clipboard.writeText(text).then(function () {
                    btn.textContent = 'Copied';
                    btn.classList.add('copied');
                    setTimeout(function () {
                        btn.textContent = 'Copy';
                        btn.classList.remove('copied');
                    }, 1400);
                }).catch(function () { btn.textContent = 'Failed'; });
            });
            pre.appendChild(btn);
        });
    }

    // ---- Tabs ----
    function wireTabs() {
        document.querySelectorAll('[data-tabs]').forEach(function (group) {
            var btns = group.querySelectorAll('.tab-btn');
            var panels = group.querySelectorAll('.tab-panel');
            btns.forEach(function (btn) {
                btn.addEventListener('click', function () {
                    btns.forEach(function (b) { b.classList.remove('active'); });
                    panels.forEach(function (p) { p.classList.remove('active'); });
                    btn.classList.add('active');
                    var target = group.querySelector('.tab-panel[data-tab="' + btn.dataset.tab + '"]');
                    if (target) target.classList.add('active');
                });
            });
        });
    }

    // ---- TOC active highlight ----
    function wireToc() {
        var toc = document.querySelector('.toc');
        if (!toc) return;
        var links = Array.prototype.slice.call(toc.querySelectorAll('a[href^="#"]'));
        if (!links.length) return;
        var byId = {};
        var targets = [];
        links.forEach(function (a) {
            var id = a.getAttribute('href').slice(1);
            var el = document.getElementById(id);
            if (el) { byId[id] = a; targets.push(el); }
        });
        var visible = new Set();
        var io = new IntersectionObserver(function (entries) {
            entries.forEach(function (e) {
                if (e.isIntersecting) visible.add(e.target.id);
                else visible.delete(e.target.id);
            });
            links.forEach(function (l) { l.classList.remove('active'); });
            for (var i = 0; i < targets.length; i++) {
                if (visible.has(targets[i].id)) { byId[targets[i].id].classList.add('active'); break; }
            }
        }, { rootMargin: '-100px 0px -65% 0px', threshold: 0 });
        targets.forEach(function (t) { io.observe(t); });
    }

    document.addEventListener('DOMContentLoaded', function () {
        addCopyButtons();
        wireTabs();
        wireToc();
    });
})();
