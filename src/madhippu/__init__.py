"""madhippu: financial statements out of Indian annual reports.

    import madhippu
    report = madhippu.parse("annual-report.pdf")
    data = madhippu.to_dict(report)
"""

__version__ = "0.1.0"

from madhippu.report import Report, parse, parse_pages, to_dict  # noqa: E402

__all__ = ["Report", "parse", "parse_pages", "to_dict", "__version__"]
