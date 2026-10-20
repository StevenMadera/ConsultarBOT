# PaginasBOT para Linux Mint

La misma consulta de PaginasBOT (Windows), preparada para Linux Mint 22 de 64 bits. En Linux Mint 21 el instalador agrega Python 3.12.

## Instalación (una vez por equipo)
1. Copia la carpeta `PaginasBOT-Linux` completa al equipo (por USB o desde el repositorio).
2. Ábrela, clic derecho en un espacio vacío → **Abrir en una terminal**, y escribe:
   ```
   python3 instalar.py
   ```
   Pide tu contraseña de usuario para instalar lo del sistema. Copia el programa a `~/BOTs/PaginasBOT-Linux`, instala Google Chrome, Tesseract OCR, las librerías y el Chromium de Playwright, y crea el acceso **PaginasBOT** en el menú (Oficina) y en el Escritorio. Volver a ejecutarlo repara la instalación sin borrar los informes.

Requisitos: internet y la sesión normal de Linux Mint (X11), no la de Wayland.

## Uso
Igual que en Windows: abre **PaginasBOT**, copia la fila en Excel o LibreOffice Calc (nombre, documento, empresa, fecha consulta, quien consulta, fecha expedición, sexo) y pulsa «Consultar». La ventanita avisa con un sonido cuando hay que escribir un captcha, marcar «No soy un robot» o pasar una verificación.

- FBI y Consilium se abren en un Google Chrome propio del bot (perfil aparte en `~/.config/PaginasBOT/chrome`; no toca tu Chrome). La primera vez, en Consilium pulsa «Aceptar solo las cookies necesarias» y luego «Listo»: Chrome lo recuerda.
- Los informes quedan en `Documentos/PAGINAS/<MES>/<fecha>`; «Abrir PDF» y «Abrir carpeta» los muestran.
- El registro de cada consulta está en `~/BOTs/PaginasBOT-Linux/log.txt` («Ver registro» en la ventanita).
- Al abrir, si GitHub tiene una versión nueva de esta carpeta, se actualiza sola y vuelve a abrir.
- Procuraduría, RUNT y SIMIT rechazan las conexiones por VPN, igual que en Windows.
