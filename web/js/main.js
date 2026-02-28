// Agent Registry Documentation Site - JavaScript

document.addEventListener('DOMContentLoaded', () => {
    // Mobile Navigation Toggle
    const navToggle = document.querySelector('.nav-toggle');
    const navLinks = document.querySelector('.nav-links');

    if (navToggle && navLinks) {
        navToggle.addEventListener('click', () => {
            navLinks.classList.toggle('active');
        });

        // Close mobile menu when clicking a link
        navLinks.querySelectorAll('a').forEach(link => {
            link.addEventListener('click', () => {
                navLinks.classList.remove('active');
            });
        });
    }

    // Copy Code Buttons
    const copyButtons = document.querySelectorAll('.copy-btn');

    copyButtons.forEach(btn => {
        btn.addEventListener('click', async () => {
            const codeBlock = btn.parentElement.querySelector('pre code');
            const text = codeBlock.textContent;

            try {
                await navigator.clipboard.writeText(text);
                btn.textContent = 'Copied!';
                btn.classList.add('copied');

                setTimeout(() => {
                    btn.textContent = 'Copy';
                    btn.classList.remove('copied');
                }, 2000);
            } catch (err) {
                console.error('Failed to copy:', err);
            }
        });
    });

    // Smooth Scroll for Navigation Links
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            const target = document.querySelector(this.getAttribute('href'));
            if (target) {
                const navHeight = document.querySelector('.nav').offsetHeight;
                const targetPosition = target.offsetTop - navHeight - 20;
                window.scrollTo({
                    top: targetPosition,
                    behavior: 'smooth'
                });
            }
        });
    });

    // API Endpoint Details Toggle
    const endpointDetails = document.querySelectorAll('.endpoint-details summary');

    endpointDetails.forEach(summary => {
        summary.addEventListener('click', () => {
            const details = summary.parentElement;
            const isOpen = details.hasAttribute('open');
            
            // Close all other details
            document.querySelectorAll('.endpoint-details[open]').forEach(openDetails => {
                if (openDetails !== details) {
                    openDetails.removeAttribute('open');
                }
            });
        });
    });

    // Active Navigation Highlighting
    const sections = document.querySelectorAll('section[id]');
    const navLinksItems = document.querySelectorAll('.nav-links a');

    function highlightNav() {
        const scrollPos = window.scrollY + 100;

        sections.forEach(section => {
            const top = section.offsetTop;
            const height = section.offsetHeight;
            const id = section.getAttribute('id');
            const correspondingLink = document.querySelector(`.nav-links a[href="#${id}"]`);

            if (correspondingLink) {
                if (scrollPos >= top && scrollPos < top + height) {
                    correspondingLink.classList.add('active');
                } else {
                    correspondingLink.classList.remove('active');
                }
            }
        });
    }

    window.addEventListener('scroll', highlightNav);
    highlightNav(); // Initial check

    // Code Block Language Tabs
    const langTabs = document.querySelectorAll('.lang-tab');

    langTabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const container = tab.parentElement;
            const stepContent = container.closest('.step-content');
            
            // Remove active class from all tabs in this container
            container.querySelectorAll('.lang-tab').forEach(t => t.classList.remove('active'));
            
            // Add active class to clicked tab
            tab.classList.add('active');
            
            // In a real implementation, you would swap the code content here
            // For now, this is a UI demo
        });
    });

    // Intersection Observer for Fade-in Animations
    const observerOptions = {
        root: null,
        rootMargin: '0px',
        threshold: 0.1
    };

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.style.opacity = '1';
                entry.target.style.transform = 'translateY(0)';
            }
        });
    }, observerOptions);

    // Observe elements that should animate in
    document.querySelectorAll('.feature-card, .api-endpoint, .topic').forEach(el => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(20px)';
        el.style.transition = 'opacity 0.5s ease-out, transform 0.5s ease-out';
        observer.observe(el);
    });

    // Table of Contents Generation (for API section)
    const apiSection = document.querySelector('#api');
    if (apiSection) {
        const apiSections = apiSection.querySelectorAll('.api-section > h3');
        
        if (apiSections.length > 3) {
            const toc = document.createElement('div');
            toc.className = 'api-toc';
            toc.innerHTML = '<h4>API Sections</h4><ul></ul>';
            
            apiSections.forEach(section => {
                const id = section.textContent.toLowerCase().replace(/\s+/g, '-');
                section.id = id;
                
                const li = document.createElement('li');
                li.innerHTML = `<a href="#${id}">${section.textContent}</a>`;
                toc.querySelector('ul').appendChild(li);
            });
            
            apiSection.insertBefore(toc, apiSection.querySelector('.api-sections'));
        }
    }

    // Keyboard Shortcuts
    document.addEventListener('keydown', (e) => {
        // Press '/' to focus search (future feature)
        if (e.key === '/' && !e.ctrlKey && !e.metaKey) {
            // Search functionality to be implemented
        }
    });

    // Print Styles
    window.addEventListener('beforeprint', () => {
        document.body.classList.add('printing');
    });

    window.addEventListener('afterprint', () => {
        document.body.classList.remove('printing');
    });

    console.log('Agent Registry Documentation loaded 🚀');
});

// Utility: Debounce function for performance
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Utility: Escape HTML for security
function escapeHtml(unsafe) {
    return unsafe
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
