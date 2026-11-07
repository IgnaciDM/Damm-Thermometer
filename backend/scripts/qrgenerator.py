from pathlib import Path

import qrcode

# Texto o enlace que contendrá el QR
data = "https://www.canva.com/es_es/generar-codigo-qr/"

# Crear instancia de QRCode
qr = qrcode.QRCode(
    version=1,
    error_correction=qrcode.constants.ERROR_CORRECT_L,
    box_size=10,
    border=4,
)
qr.add_data(data)
qr.make(fit=True)

# Crear imagen y guardarla
img = qr.make_image(fill_color="black", back_color="white")
output = Path(__file__).resolve().parents[2] / "ui" / "assets" / "codigo_qr.png"
output.parent.mkdir(parents=True, exist_ok=True)
img.save(output)
