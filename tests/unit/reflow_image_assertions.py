"""Compare retained crop pixels independently of optional paper framing."""
import io
from PIL import Image


def figure_name(package, stem):
    names = [name for name in package.namelist() if name.startswith('OEBPS/images/'+stem+'.')]
    assert len(names) == 1, names
    return names[0]


def assert_retained_pixels(actual, expected):
    with Image.open(io.BytesIO(actual)) as decoded, Image.open(io.BytesIO(expected)) as original:
        decoded, original = decoded.convert('RGB'), original.convert('RGB')
        if decoded.size == original.size:
            assert decoded.tobytes() == original.tobytes()
        else:
            assert decoded.size == (original.width+16, original.height+16)
            assert decoded.crop((8,8,8+original.width,8+original.height)).tobytes() == original.tobytes()
            for box in ((0,0,decoded.width,8),(0,decoded.height-8,decoded.width,decoded.height),
                        (0,8,8,decoded.height-8),(decoded.width-8,8,decoded.width,decoded.height-8)):
                edge = decoded.crop(box)
                assert edge.tobytes() == Image.new('RGB',edge.size,'white').tobytes()
