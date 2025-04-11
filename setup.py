from setuptools import setup, find_packages

setup(
    name="dsign",
    version="1.0.0",
    description="Digital Signage System with Flask and MPV player",
    include_package_data=True,
    install_requires=[
        'flask>=2.0.0',
        'flask-sqlalchemy>=2.5.0',
        'flask-login>=0.5.0',
        'flask-socketio>=5.0.0',
        'flask-bcrypt>=1.0.0',
        'eventlet>=0.30.0',
        'python-mpv>=0.5.0',
        'psutil>=5.8.0',
    ],
    extras_require={
        'dev': [
            'pytest>=6.0.0',
            'pytest-cov>=2.0.0',
            'flake8>=3.9.0',
        ],
    },
    entry_points={
        'console_scripts': [
            'dsign=dsign.server:run_server',
        ],
    },
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: End Users/Desktop',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
    ],
    python_requires='>=3.8',
)