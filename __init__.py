# jarvis/__init__.py

__version__ = "1.0.0"

# Expose the main entry point via a function to avoid 
# importing 'main' immediately when the package is initialized.
def start():
    from .main import main
    main()