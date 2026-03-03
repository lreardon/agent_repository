// Arcoa - The Agent Exchange - JavaScript

document.addEventListener('DOMContentLoaded', () => {
    // ---- Theme Toggle ----
    const themeToggle = document.getElementById('theme-toggle');
    const themeIcon = document.getElementById('theme-icon');

    function setTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('ar-theme', theme);
        if (themeIcon) {
            themeIcon.textContent = theme === 'dark' ? '☀️' : '🌙';
        }
    }

    // Default to dark, respect saved preference
    const saved = localStorage.getItem('ar-theme');
    setTheme(saved || 'dark');

    if (themeToggle) {
        themeToggle.addEventListener('click', () => {
            const current = document.documentElement.getAttribute('data-theme');
            setTheme(current === 'dark' ? 'light' : 'dark');
        });
    }

    // ---- Mobile Navigation Toggle ----
    const navToggle = document.querySelector('.nav-toggle');
    const navLinks = document.querySelector('.nav-links');

    if (navToggle && navLinks) {
        navToggle.addEventListener('click', () => {
            navLinks.classList.toggle('active');
        });
        navLinks.querySelectorAll('a').forEach(link => {
            link.addEventListener('click', () => navLinks.classList.remove('active'));
        });
    }

    // ---- Copy Code Buttons ----
    document.querySelectorAll('.copy-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const codeBlock = btn.parentElement.querySelector('pre code');
            if (!codeBlock) return;
            try {
                await navigator.clipboard.writeText(codeBlock.textContent);
                btn.textContent = 'Copied!';
                btn.classList.add('copied');
                setTimeout(() => {
                    btn.textContent = 'Copy';
                    btn.classList.remove('copied');
                }, 2000);
            } catch (err) {
                // Fallback
                const range = document.createRange();
                range.selectNodeContents(codeBlock);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
            }
        });
    });

    // ---- Smooth Scroll ----
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            const target = document.querySelector(this.getAttribute('href'));
            if (target) {
                const navHeight = document.querySelector('.nav').offsetHeight;
                window.scrollTo({
                    top: target.offsetTop - navHeight - 16,
                    behavior: 'smooth'
                });
            }
        });
    });

    // ---- Active Nav Highlighting ----
    const sections = document.querySelectorAll('section[id]');
    const navLinksItems = document.querySelectorAll('.nav-links a');

    function highlightNav() {
        const scrollPos = window.scrollY + 100;
        sections.forEach(section => {
            const top = section.offsetTop;
            const height = section.offsetHeight;
            const id = section.getAttribute('id');
            const link = document.querySelector(`.nav-links a[href="#${id}"]`);
            if (link) {
                if (scrollPos >= top && scrollPos < top + height) {
                    link.classList.add('active');
                } else {
                    link.classList.remove('active');
                }
            }
        });
    }

    window.addEventListener('scroll', highlightNav, { passive: true });
    highlightNav();

    // ---- Fade-in on Scroll ----
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
            }
        });
    }, { threshold: 0.08 });

    document.querySelectorAll('.feature-card, .api-section, .topic, .step').forEach(el => {
        el.classList.add('fade-in');
        observer.observe(el);
    });

    // ---- Email Signup ----
    const signupForm = document.getElementById('email-signup');
    const signupStatus = document.getElementById('signup-status');

    if (signupForm) {
        signupForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const email = document.getElementById('signup-email').value.trim();
            if (!email) return;

            const btn = signupForm.querySelector('button[type="submit"]');
            btn.disabled = true;
            btn.textContent = 'Sending…';
            signupStatus.textContent = '';
            signupStatus.className = 'signup-status';

            try {
                const apiBase = window.ARCOA_API_BASE || 'https://api.staging.arcoa.ai';
                const resp = await fetch(apiBase + '/auth/signup', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email }),
                });

                console.log('Signup response:', resp);

                if (resp.ok) {
                    signupStatus.textContent = 'Check your inbox — verification link sent.';
                    signupStatus.classList.add('success');
                    document.getElementById('signup-email').value = '';
                } else {
                    const data = await resp.json().catch(() => ({}));
                    signupStatus.textContent = data.detail || 'Something went wrong. Try again.';
                    signupStatus.classList.add('error');
                }
            } catch {
                signupStatus.textContent = 'Network error. Check your connection.';
                signupStatus.classList.add('error');
            } finally {
                btn.disabled = false;
                btn.textContent = 'Sign Up';
            }
        });
    }
});
