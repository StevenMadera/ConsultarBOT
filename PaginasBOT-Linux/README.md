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
Igual que en Windows: abre **PaginasBOT**, copia la fila en Excel o LibreOffice Calc (nombre, documento, empresa, ciudad, fecha consulta, quien consulta, fecha expedición, sexo) y pulsa «Consultar». También se pueden copiar solo las primeras columnas, desde el nombre y el documento: lo demás queda en blanco para escribirlo a mano. La ventanita avisa con un sonido cuando hay que escribir un captcha, marcar «No soy un robot» o pasar una verificación. Si no se tiene la fecha de expedición, escribe `0`: se omiten Registraduría, RNMC e Inhabilidades (el RNMC sí se consulta a un NIT), no se marca nada en la portada y al final la ventanita avisa cuáles se omitieron.

- FBI y Consilium se abren en un Google Chrome propio del bot (perfil aparte en `~/.config/PaginasBOT/chrome`; no toca tu Chrome) apenas empieza la consulta, y cada una se captura en cuanto carga, entre un captcha y otro. La primera vez, en Consilium pulsa «Aceptar solo las cookies necesarias» y luego «Listo»: Chrome lo recuerda.
- Las ventanas de captcha también son de Google Chrome (si no abre, del Chromium del bot), sin la marca de navegador automatizado que hace que Google pida más imágenes. Cada 5 consultas, o si la casilla «No soy un robot» tardó más de un minuto o no se marcó, las ventanas de Antecedentes e Inhabilidades se cambian por unas nuevas, sin las cookies de Google, al terminar la consulta.
- Los informes quedan en `Documentos/PAGINAS/<MES>/<fecha>`; «Abrir PDF» y «Abrir carpeta» los muestran.
- El registro de cada consulta está en `~/BOTs/PaginasBOT-Linux/log.txt` («Ver registro» en la ventanita).
- Al abrir, si GitHub tiene una versión nueva de esta carpeta, se actualiza sola y vuelve a abrir. Después muestra una ventana con todo lo que cambió desde la versión que tenía el equipo, tomado de `novedades.txt` (cada versión se muestra una sola vez; si son muchas, la lista tiene barra; un equipo recién instalado no la muestra; lo ya visto queda en `~/.config/PaginasBOT/novedades_vistas.txt`). Antes de subir cambios que noten los usuarios, agrega arriba en ese archivo un título `== fecha ==` nuevo y una línea `- ...` por cambio.
- Procuraduría, RUNT y SIMIT rechazan las conexiones por VPN, igual que en Windows.
- Al abrir PaginasBOT, el RUNT (unos 12 MB de código) carga primero y solo, hasta 20 s; después cargan las otras ventanas de captcha, SIMIT, la lista de la ONU y EUR-Lex. Su caché queda en `~/.config/PaginasBOT/perfil_runt`; si hay dos PaginasBOT abiertos a la vez, el segundo carga el RUNT sin caché.
- SIMIT tarda en abrir: su formulario se deja listo al abrir PaginasBOT y después de cada consulta, y la siguiente lo usa si se cargó hace menos de 10 minutos.
- En la lista de la ONU se busca el número de documento; en EUR-Lex («Consilium Europa»), el nombre completo. Como Ctrl+F: sin distinguir mayúsculas ni tildes.
- RNMC revisa que la fecha de expedición quedó escrita tal cual antes de enviar; si el sitio responde que no es correcta, consulta una vez más. Inhabilidades se imprime cuando la página terminó de cargar.
- La primera consulta de cada día borra las imágenes y PDF de trabajo de `capturas/` de días anteriores; los informes no se tocan.
