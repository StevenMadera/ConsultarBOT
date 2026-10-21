# PaginasBOT

Consulta automática de una persona en varias páginas públicas, con captura de cada resultado y un PDF final.

## Requisitos
- Windows 10/11 con internet
- Edge o Chrome como navegador por defecto

## Instalación (una sola vez por equipo)
Doble clic en `Instalar PaginasBOT.bat` (está en la raíz del repositorio; basta enviar ese archivo). Instala Python 3.14 para el usuario si falta, descarga el programa en `C:\Users\<usuario>\BOTs`, instala las librerías, Chromium y Tesseract OCR, y crea el acceso directo **PaginasBOT** en el Escritorio y en el menú Inicio. Si algo se daña, volver a ejecutarlo lo repara sin borrar `salidas/`.

Si Tesseract falta, se descarga el instalador de UB Mannheim y se verifica su SHA-256 antes de ejecutarlo. Se instala para el usuario en `%LOCALAPPDATA%\Programs\Tesseract-OCR`, sin depender de WinGet. Windows puede pedir autorización para abrir el instalador. Se comprueba que funcione y tenga el idioma `eng`; si falla, se muestra el motivo. También se reconocen instalaciones existentes en el PATH y en las carpetas habituales de Windows.

Con la carpeta ya descargada (por ejemplo, con `git clone`): doble clic en `Instalar.bat` de la raíz. Si se mueve la carpeta, se vuelve a ejecutar.

Sin instalador (Python 3.12 o superior):
```
py -m pip install -r requirements.txt
py -m playwright install chromium
```
Instala también Tesseract OCR para Windows, con el idioma inglés (`eng`), y añádelo al PATH o usa una de las carpetas habituales indicadas arriba.

## Uso
Doble clic en el acceso directo **PaginasBOT** (o en `PaginasBOT.pyw`). Se abre una ventanita en el centro de la pantalla, siempre encima de las demás.

1. Copia la fila en Excel: nombre, documento, empresa, fecha consulta, quien consulta, fecha expedicion, sexo (ej. `Santiago Flores Alonso | 1000858557 | FALABELLA CEDI | 16/09/2026 | STEVEN | 25/06/2020 | H`). Los datos se llenan solos (también con Ctrl+V en cualquier casilla, o escribiéndolos). Pulsa Enter o «Consultar». Sexo: `H` (hombre) o `M` (mujer); a las mujeres no se les consulta la libreta militar y su informe usa `Plantilla/Mujer.pdf`. Si la empresa es GALLAGHER, el informe usa `Plantilla/FORMATO PÁGINAS GALLAGHER.pdf` (hombres y mujeres; a la mujer la libreta se le marca «No Aplica») y se consultan además InSight Crime, Junta de Contadores, DIAN, SAMM, BID, Canadá y OFAC (Tesoro). Si el documento no indica tipo, es CC; tipos: CC, CE, PEP, PPT, NIT, TI (ej. `CE 123456`).
2. La ventanita muestra el avance de cada sitio. Cuando te necesita, suena un aviso:
   - RUNT y Registraduría: la imagen del código aparece en la ventanita; escríbelo y pulsa Enter.
   - Antecedentes e Inhabilidades (Policía): la ventanita se pasa a la esquina superior derecha; marca "No soy un robot" en la ventana del navegador y el bot sigue solo.
   - FBI o Consilium (verificación de Cloudflare o cookies): haz el paso en el navegador y pulsa «Listo».
   - SIMIT con varias personas: elige la que corresponde.

   Al empezar se abren minimizadas dos ventanas de Chrome (FBI y Consilium): no las cierres. El bot cierra cada ventana en cuanto termina su captura, también si falla la consulta, sin esperar a que termine todo el informe. Las ventanas que ya tenías abiertas no se cierran.
3. Al terminar: «Abrir PDF», «Abrir carpeta» y lo que no se pudo consultar. Para otra persona, copia la siguiente fila. El PDF final queda en `Documentos\PAGINAS\<MES>\<DD-MM-AAAA>\`, por ejemplo `Documentos\PAGINAS\OCTUBRE\20-10-2026\`. Se usa el día real de generación, no la fecha de consulta ingresada en Excel. Se respeta la ubicación de Documentos configurada en Windows, incluso si está en OneDrive o redirigida. Las carpetas se crean automáticamente; si el nombre ya existe, se añade un contador sin sobrescribirlo. Las capturas y PDF intermedios siguen en `capturas/`; los informes antiguos de `salidas/` no se mueven ni se borran.
4. Cierra la ventanita para salir; si hay una consulta en curso, pregunta antes.

Sin ventanita, en la consola: `py main.py` (los códigos se escriben en la consola y Enter sin texto sale).

## Actualizaciones
Al abrir, PaginasBOT revisa si GitHub tiene una versión nueva. Si la hay (y todavía no se empezó una consulta), muestra «Actualizando…», baja los cambios (con `git pull` si se instaló con `git clone`; si no, solo los archivos que cambiaron), vuelve a ejecutar el instalador —que instala Tesseract OCR directamente si falta— y se abre de nuevo sola. `salidas/`, `capturas/` y `log.txt` no se tocan. Donde se programa (cambios sin commit o commits sin subir) nunca se actualiza.

## Antes de ejecutar
- Cada sitio carga en una sola pestaña. Se espera hasta 2 minutos a que aparezca su formulario (el RUNT, con todo cargando a la vez, puede tardar más de un minuto); si no aparece, se detiene la carga y se recarga una vez, con otros 2 minutos. Solo si tampoco abre se marca como fuera de servicio. En RUNT y SIMIT, desde la segunda consulta se vuelve a su formulario antes de recargar. State.gov y Justice esperan un minuto en el navegador automático y, si no abren, se capturan en Chrome. Los envíos de consulta y el tiempo de resolver captchas quedan fuera de esta espera.
- En Inhabilidades se espera a que aparezca la casilla real de reCAPTCHA, no solo su marco. Si la casilla queda cargando al intentar resolverla, se vuelve a cargar el formulario y se rellenan documento, fecha de expedición, empresa, NIT y términos antes de pedir el captcha otra vez (hasta tres intentos).
- La captura de Procuraduría usa la fecha de consulta ingresada en Excel en el campo «Fecha de consulta». Reconoce fechas numéricas y el formato del portal («martes, octubre 20, 2026»); recalcula el día de la semana y conserva la hora. El cambio es local, antes de capturar: no modifica el registro del portal ni otras fechas del resultado. Si no se reconoce ese campo, se conserva la fecha original y se avisa en `log.txt`.
- Cierra videos flotantes u otras ventanas encima del navegador: salen en la captura del FBI y de Consilium. La ventanita de PaginasBOT no sale (Windows la excluye de las capturas de pantalla; tampoco aparece en tus recortes ni al compartir pantalla).
- Si falla una página, el error queda en `log.txt` y el bot sigue con las demás.

## Rendimiento
- Se precargan hasta dos formularios con captcha a la vez, sin hacer esperar todos al RUNT. Una precarga se reutiliza una sola vez si el formulario sigue listo y tiene menos de 45 segundos; se conserva su antigüedad real para no alargar artificialmente la vigencia del captcha. Repetir una consulta o un captcha fuerza una carga nueva.
- Las pestañas de recuperación se abren en segundo plano, sin activar su ventana ni tapar el captcha que se está resolviendo. Solo se muestra un captcha a la vez; el primero listo puede pasar sin esperar a que carguen los demás.
- Cada consulta automática ocupa su propio cupo y contexto aislado. La cola permite entre dos y cuatro consultas simultáneas según CPU y RAM libre, reservando recursos para los captchas; una página lenta no retiene el cupo de todo su grupo.
- Antes de las capturas ajustadas se comprueba estabilidad del texto y tamaño, carga de imágenes y fuentes y finalización de indicadores y animaciones finitas. No se espera a que desaparezca todo el tráfico de red (la analítica puede seguir activa). Se conservan los márgenes específicos de visores PDF y de las capturas de FBI/Consilium.
- El OCR del RUNT se ejecuta una sola vez por imagen y fuera del bucle de consultas, manteniendo la entrada manual de respaldo.
- `log.txt` incluye líneas `Rendimiento <sitio>` con segundos de `cola`, `carga`, `usuario`, `captura`, `respuesta_procesamiento` y `total`, incluso si hay errores. La espera de reCAPTCHA está en `usuario`; `respuesta_procesamiento` es el tiempo restante, no una medición exclusiva del servidor. Las precargas se registran por separado como `precarga_<sitio>`. Estos tiempos permiten comparar varias ejecuciones reales sin prometer un tiempo fijo para portales externos.
