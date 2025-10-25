// Mobile Navigation Toggle
const navToggle = document.querySelector('.nav-toggle');
const navMenu = document.querySelector('.nav-menu');

if (navToggle && navMenu) {
    navToggle.addEventListener('click', () => {
        navMenu.classList.toggle('active');
        navToggle.classList.toggle('active');
    });

    // Close mobile menu when clicking on a link
    document.querySelectorAll('.nav-link').forEach(link => {
        link.addEventListener('click', () => {
            navMenu.classList.remove('active');
            navToggle.classList.remove('active');
        });
    });
}

// Smooth scrolling for anchor links
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
        e.preventDefault();
        const target = document.querySelector(this.getAttribute('href'));
        if (target) {
            const offsetTop = target.offsetTop - 80; // Account for fixed navbar
            window.scrollTo({
                top: offsetTop,
                behavior: 'smooth'
            });
        }
    });
});

// Navbar background on scroll
window.addEventListener('scroll', () => {
    const navbar = document.querySelector('.navbar');
    if (navbar) {
        if (window.scrollY > 50) {
            navbar.style.background = 'rgba(255, 255, 255, 0.98)';
            navbar.style.boxShadow = '0 2px 20px rgba(0, 0, 0, 0.1)';
        } else {
            navbar.style.background = 'rgba(255, 255, 255, 0.95)';
            navbar.style.boxShadow = 'none';
        }
    }
});

// Intersection Observer for animations
const observerOptions = {
    threshold: 0.1,
    rootMargin: '0px 0px -50px 0px'
};

const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            entry.target.style.opacity = '1';
            entry.target.style.transform = 'translateY(0)';
        }
    });
}, observerOptions);

// Observe elements for animation
document.addEventListener('DOMContentLoaded', () => {
    const animatedElements = document.querySelectorAll('.feature-card, .command-card, .command-category, .privacy-feature');

    animatedElements.forEach(el => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(30px)';
        el.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
        observer.observe(el);
    });
});

// Copy to clipboard functionality for code blocks
document.addEventListener('DOMContentLoaded', () => {
    const codeBlocks = document.querySelectorAll('.code-block');

    codeBlocks.forEach(block => {
        block.style.position = 'relative';
        block.style.cursor = 'pointer';

        // Add copy button
        const copyButton = document.createElement('button');
        copyButton.innerHTML = '<i class="fas fa-copy"></i>';
        copyButton.style.position = 'absolute';
        copyButton.style.top = '10px';
        copyButton.style.right = '10px';
        copyButton.style.background = 'rgba(255, 255, 255, 0.2)';
        copyButton.style.border = 'none';
        copyButton.style.color = 'white';
        copyButton.style.padding = '5px 10px';
        copyButton.style.borderRadius = '5px';
        copyButton.style.cursor = 'pointer';
        copyButton.style.fontSize = '12px';

        copyButton.addEventListener('click', (e) => {
            e.stopPropagation();
            const text = block.textContent;
            navigator.clipboard.writeText(text).then(() => {
                copyButton.innerHTML = '<i class="fas fa-check"></i>';
                setTimeout(() => {
                    copyButton.innerHTML = '<i class="fas fa-copy"></i>';
                }, 2000);
            });
        });

        block.appendChild(copyButton);
    });
});

// Search functionality for commands page
document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.placeholder = 'Search commands...';
    searchInput.style.cssText = `
        width: 100%;
        max-width: 500px;
        padding: 12px 20px;
        margin: 20px auto;
        border: 2px solid #e2e8f0;
        border-radius: 25px;
        font-size: 16px;
        display: block;
        background: white;
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
    `;

    const commandsContent = document.querySelector('.commands-content');
    if (commandsContent) {
        commandsContent.insertBefore(searchInput, commandsContent.firstChild);

        searchInput.addEventListener('input', (e) => {
            const searchTerm = e.target.value.toLowerCase();
            const commandCards = document.querySelectorAll('.command-card');

            commandCards.forEach(card => {
                const commandName = card.querySelector('h3').textContent.toLowerCase();
                const commandDesc = card.querySelector('p').textContent.toLowerCase();
                const isVisible = commandName.includes(searchTerm) || commandDesc.includes(searchTerm);

                card.style.display = isVisible ? 'block' : 'none';
            });

            // Hide/show command sections based on visible cards
            const commandSections = document.querySelectorAll('.command-section');
            commandSections.forEach(section => {
                const visibleCards = section.querySelectorAll('.command-card[style*="block"], .command-card:not([style*="none"])');
                section.style.display = visibleCards.length > 0 ? 'block' : 'none';
            });
        });
    }
});

// Lazy loading for images
document.addEventListener('DOMContentLoaded', () => {
    const images = document.querySelectorAll('img[data-src]');
    const imageObserver = new IntersectionObserver((entries, observer) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const img = entry.target;
                img.src = img.dataset.src;
                img.classList.remove('lazy');
                imageObserver.unobserve(img);
            }
        });
    });

    images.forEach(img => imageObserver.observe(img));
});

// Back to top button
document.addEventListener('DOMContentLoaded', () => {
    const backToTopButton = document.createElement('button');
    backToTopButton.innerHTML = '<i class="fas fa-arrow-up"></i>';
    backToTopButton.style.cssText = `
        position: fixed;
        bottom: 20px;
        right: 20px;
        width: 50px;
        height: 50px;
        border-radius: 50%;
        background: linear-gradient(135deg, #667eea, #764ba2);
        color: white;
        border: none;
        cursor: pointer;
        font-size: 18px;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
        opacity: 0;
        visibility: hidden;
        transition: all 0.3s ease;
        z-index: 1000;
    `;

    document.body.appendChild(backToTopButton);

    window.addEventListener('scroll', () => {
        if (window.scrollY > 300) {
            backToTopButton.style.opacity = '1';
            backToTopButton.style.visibility = 'visible';
        } else {
            backToTopButton.style.opacity = '0';
            backToTopButton.style.visibility = 'hidden';
        }
    });

    backToTopButton.addEventListener('click', () => {
        window.scrollTo({
            top: 0,
            behavior: 'smooth'
        });
    });
});

// Command card hover effects
document.addEventListener('DOMContentLoaded', () => {
    const commandCards = document.querySelectorAll('.command-card');

    commandCards.forEach(card => {
        card.addEventListener('mouseenter', () => {
            card.style.transform = 'translateX(10px) scale(1.02)';
            card.style.boxShadow = '0 10px 25px rgba(102, 126, 234, 0.2)';
        });

        card.addEventListener('mouseleave', () => {
            card.style.transform = 'translateX(0) scale(1)';
            card.style.boxShadow = 'none';
        });
    });
});

// Feature card animations
document.addEventListener('DOMContentLoaded', () => {
    const featureCards = document.querySelectorAll('.feature-card');

    featureCards.forEach(card => {
        card.addEventListener('mouseenter', () => {
            card.style.transform = 'translateY(-15px) scale(1.02)';
        });

        card.addEventListener('mouseleave', () => {
            card.style.transform = 'translateY(0) scale(1)';
        });
    });
});

// Loading animation
window.addEventListener('load', () => {
    document.body.style.opacity = '0';
    document.body.style.transition = 'opacity 0.5s ease';

    setTimeout(() => {
        document.body.style.opacity = '1';
    }, 100);
});

// Keyboard navigation
document.addEventListener('keydown', (e) => {
    // Escape key to close mobile menu
    if (e.key === 'Escape') {
        if (navMenu && navMenu.classList.contains('active')) {
            navMenu.classList.remove('active');
            navToggle.classList.remove('active');
        }
    }

    // Ctrl/Cmd + K to focus search
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        const searchInput = document.querySelector('input[type="text"]');
        if (searchInput) {
            searchInput.focus();
        }
    }
});

// Performance optimization: Debounce scroll events
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

// Apply debouncing to scroll events
const debouncedScrollHandler = debounce(() => {
    // Scroll-based animations and effects
}, 10);

window.addEventListener('scroll', debouncedScrollHandler);
