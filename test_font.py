from PyQt6.QtGui import QFontDatabase, QGuiApplication
import sys
app = QGuiApplication(sys.argv)
font_id = QFontDatabase.addApplicationFont("data/qcf_assets/fonts/QCF4_Hafs_01_W.ttf")
print("Font ID:", font_id)
if font_id != -1:
    print("Families:", QFontDatabase.applicationFontFamilies(font_id))
