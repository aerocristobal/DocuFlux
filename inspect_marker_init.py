import inspect
from marker.converters.pdf import PdfConverter
print(inspect.signature(PdfConverter.__init__))
