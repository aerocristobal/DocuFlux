#!/usr/bin/env python3
"""
Test script to verify CJK (Chinese-Japanese-Korean) PDF conversion support.
This tests that XeLaTeX and Noto CJK fonts are properly configured.

These tests require a running `docuflux-worker-1` Docker container.
They are skipped automatically in CI when the container is not available.
Run them locally with: docker-compose up worker
"""

import subprocess
import sys
import pytest


def _worker_running():
    """Return True if the docuflux-worker-1 container is accessible."""
    result = subprocess.run(
        ['docker', 'exec', 'docuflux-worker-1', 'true'],
        capture_output=True
    )
    return result.returncode == 0


pytestmark = pytest.mark.skipif(
    not _worker_running(),
    reason="docuflux-worker-1 container is not running (skipped in CI)"
)


def test_xelatex_installed():
    """Test that XeLaTeX is installed in the worker container"""
    result = subprocess.run(
        ['docker', 'exec', 'docuflux-worker-1', 'which', 'xelatex'],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, "XeLaTeX not found in worker container"
    assert '/usr/bin/xelatex' in result.stdout, f"Unexpected XeLaTeX path: {result.stdout}"
    print("✓ XeLaTeX is installed")

def test_cjk_fonts_installed():
    """Test that Noto CJK fonts are installed"""
    result = subprocess.run(
        ['docker', 'exec', 'docuflux-worker-1', 'fc-list'],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, "fc-list command failed"
    assert 'Noto Sans CJK' in result.stdout, "Noto Sans CJK fonts not found"
    assert 'Noto Serif CJK' in result.stdout, "Noto Serif CJK fonts not found"
    print("✓ Noto CJK fonts are installed")

def test_pandoc_pdf_conversion():
    """Test Pandoc PDF conversion with Chinese characters"""
    # Create test markdown with Chinese content
    test_md = """# 测试文档

这是一个中文测试文档。

## 功能特点
- 支持简体中文
- 支持繁体中文
- 支持日文和韩文

代码示例：
```python
print("你好，世界！")
```
"""

    # Write test file to container
    subprocess.run(
        ['docker', 'exec', '-i', 'docuflux-worker-1', 'bash', '-c', 'cat > /tmp/test_cjk.md'],
        input=test_md.encode(),
        check=True
    )

    # Convert to PDF using Pandoc with XeLaTeX
    result = subprocess.run(
        [
            'docker', 'exec', 'docuflux-worker-1',
            'pandoc', '/tmp/test_cjk.md', '-o', '/tmp/test_cjk.pdf',
            '--pdf-engine=xelatex',
            '--variable', 'mainfont=Noto Sans CJK SC',
            '--variable', 'CJKmainfont=Noto Sans CJK SC',
            '--variable', 'monofont=DejaVu Sans Mono',
            '--variable', 'geometry:margin=1in'
        ],
        capture_output=True,
        text=True,
        timeout=60
    )

    if result.returncode != 0:
        print(f"✗ Pandoc conversion failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
        return False

    # Check if PDF was created
    result = subprocess.run(
        ['docker', 'exec', 'docuflux-worker-1', 'ls', '-lh', '/tmp/test_cjk.pdf'],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print("✗ PDF file was not created")
        return False

    print("✓ Pandoc PDF conversion with CJK characters succeeded")
    print(f"  PDF size: {result.stdout.split()[4]}")
    return True

def main():
    """Run all tests"""
    print("Testing CJK (Chinese-Japanese-Korean) PDF Conversion Support\n")
    print("=" * 60)

    try:
        test_xelatex_installed()
        test_cjk_fonts_installed()
        if not test_pandoc_pdf_conversion():
            sys.exit(1)

        print("\n" + "=" * 60)
        print("✓ All CJK conversion tests passed!")
        print("\nChinese character support is working correctly.")
        print("Users can now convert markdown files with CJK characters to PDF.")

    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"\n✗ Command failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
