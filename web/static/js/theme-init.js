(function() {
    const apply = (t) => {
        let dark = t === 'dark' || (t === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
        document.documentElement.classList.toggle('dark-theme', dark);
    };
    apply(localStorage.getItem('theme') || 'system');
    window.applyTheme = apply;
})();
