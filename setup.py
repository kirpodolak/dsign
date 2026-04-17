from setuptools import setup, find_packages

setup(
    name="dsign",
    version="1.0.0",
    description="Digital Signage System with Flask and MPV player",
    include_package_data=True,
    install_requires=[
        'Flask>=3.0.0',
        'Flask-Bcrypt>=1.0.1',
        'Flask-Login>=0.6.3',
        'Flask-SocketIO>=5.3.6',
        'Flask-SQLAlchemy>=3.1.1',
        'Flask-WTF>=1.2.1',
        'Pillow>=10.2.0',
        'PyJWT>=2.8.0',
        'SQLAlchemy>=2.0.0',
        'WTForms>=3.1.2',
        'eventlet>=0.35.1',
        'requests>=2.32.3',
        'Werkzeug>=3.0.1',
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
