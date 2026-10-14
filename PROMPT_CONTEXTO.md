# Prompt de contexto para continuar el proyecto en otro chat

Copia todo lo que sigue y pégalo como primer mensaje del nuevo chat.

---

Estás continuando un proyecto llamado **ConsultarBot** (Python, Windows). Responde siempre en español, de forma breve e impersonal. No crees archivos de documentación salvo que te lo pida. Antes de editar, lee el archivo. Verifica tus cambios con una prueba real y borra después los scripts de prueba.

## Objetivo
Dada una fila de Excel con `nombre, documento, empresa, fecha consulta, quien consulta, fecha expedicion` (ej. `Santiago Flores Alonso | 1000858557 | FALABELLA CEDI | 16/09/2026 | STEVEN | 25/06/2020`; sin tipo de documento es CC), el bot consulta la persona en varias páginas públicas, toma una captura de cada resultado y las pega en una plantilla PDF (`Plantilla/Hombre.pdf`), generando `salidas/<numero> - <nombre>.pdf`. Cada captura debe mostrar tres cosas: el logo de la entidad, los datos de consulta y el resultado. La captura debe llenar el ancho del recuadro del PDF, mantener la proporción y quedar centrada.

## Estructura
- `main.py`: entrada (`py main.py`). Pide los datos por consola, lanza las consultas en paralelo, arma el PDF.
- `comun.py`: `CAPTURAS` y `log()` (escribe en consola y en `log.txt`).
- `captura_navegador.py`: abre una URL en el navegador por defecto del usuario (sin controlarlo), detecta la ventana por título, recorta solo el área de la página (subventana `Chrome_RenderWidgetHostHWND`), espera a que deje de cambiar y captura con `mss`. Pide pasar la verificación de Cloudflare si el título de alguna ventana la indica. `abrir_en_ventana` abre la página de antemano en una ventana propia minimizada, para capturarla después ya cargada.
- `consulta_fbi.py`, `consulta_consilium.py`, `consulta_terroristas.py` y `consulta_justicia.py`: URL y título de cada página y su captura en el navegador del usuario.
- `Plantilla/`: `Hombre.pdf` (8 páginas) y `consolidatedLegacyByNAME.html` (lista consolidada de la ONU).
- `capturas/`, `salidas/`, `log.txt`: generados por el bot, no se suben a Git.
- `requirements.txt`: playwright, pymupdf, mss, pygetwindow. Además hay que ejecutar `py -m playwright install chromium`.

## Cómo está hecho `main.py`
- Una clase `Trabajador` (hilo con su propio navegador Playwright, que se abre con la primera tarea y se reutiliza; solo se recarga la página antes de cada consulta). Cada página es una función `consultar_*(page, persona) -> Path` y un trabajador en el diccionario `trabajadores`.
- El captcha del RUNT lo escribe el usuario en la consola (cola `solicitudes_captcha`). No se resuelve automáticamente: es una decisión tomada, no se debe automatizar.
- La consola toma el foco al pedir el captcha (`enfocar_consola`). No se fija encima de las demás ventanas: así tapaba y bloqueaba la parte baja de la pantalla.
- El FBI y Consilium se abren en Chrome al empezar la consulta (ventanas minimizadas que cargan mientras se resuelven los captchas) y se capturan al final, porque necesitan la pantalla y pueden pedir la verificación manual. State.gov y Justice van en el navegador automático; solo si este falla se capturan en Chrome.
- `UBICACION_PDF` indica dónde va cada captura: `(página 0-based, hueco)`. Huecos: `arriba` (entre los dos cuadros de título), `abajo` (desde el segundo cuadro hasta el pie), `unico` (página con un solo cuadro). Las zonas se calculan leyendo los cuadros grises de la plantilla (`zona_pdf`), nunca con coordenadas fijas.
- `captura_ajustada` / `ajustar_ventana_a_recuadro`: ensanchan la ventana del navegador hasta que la página completa tenga la proporción del recuadro; si la página es muy larga (ancho > `ANCHO_MAXIMO`), captura solo el inicio.
- Nombre del PDF: `<numero> - <nombre>.pdf`; si existe, se añade ` (2)`, ` (3)`...

## Páginas implementadas y su ubicación en el PDF
| Página de plantilla | Sitio | Método | Ubicación |
|---|---|---|---|
| 1 Procuraduría | procuraduria.gov.co (formulario en iframe) | Playwright headless con user agent de Chrome; responde la pregunta de verificación (aritmética, letras/dígitos del documento o nombre, capitales de departamentos) | pág. 2, arriba |
| 2 RUNT | portalpublico.runt.gov.co | Playwright visible y maximizado; el usuario escribe el captcha; 3 reintentos; usa primer apellido (heurística) | pág. 2, abajo |
| 3 SIMIT | fcm.org.co/simit | Playwright headless; contrae la pestaña "Herramientas de accesibilidad" (elemento en shadow DOM, el sitio recuerda su estado) | pág. 3, arriba |
| 4 Libreta militar | definicion.libretamilitar.mil.co | Playwright headless con `channel="chromium"` (headless nuevo, necesario para el visor de PDF del resultado) | pág. 3, abajo |
| 5 OFAC | sanctionssearch.ofac.treas.gov | Playwright; Type=Individual, Country=Colombia, ID=número | pág. 4, arriba |
| 6 ONU | `consolidatedLegacyByNAME.html` local | Se busca el número en el documento; se dibuja una barra tipo Ctrl+F con el conteo real y se resaltan coincidencias | pág. 4, abajo |
| 7 GOV.UK | gov.uk | Rechaza cookies, busca el nombre | pág. 5, arriba |
| 8 FBI | fbi.gov/wanted/fugitives?SearchableText=<nombre> | Navegador del usuario + captura de pantalla (Cloudflare bloquea la automatización; no se debe evadir) | pág. 5, abajo |
| 9 Dpto. de Justicia | justice.gov | Playwright headless (si falla, navegador del usuario); busca el nombre | pág. 6, arriba |
| 12 EUMOSTWANTED | eumostwanted.eu | Playwright; rechaza cookies y busca el nombre | pág. 7, abajo |
| 13 Lista UE terroristas | consilium.europa.eu/es/search/?query=<nombre> | Igual que el FBI (Cloudflare) | pág. 8, único |

Pendientes que el usuario pidió saltar por ahora (su espacio ya existe en la plantilla): **Página 10** (TERRORIST DESIGNATIONS AND STATE SPONSORS OF TERRORISM, pág. 6 abajo) y **Página 11** (CONSOLIUM EUROPA, pág. 7 arriba). La página 1 de la plantilla lista además las páginas 14 a 19 (Boletín de deudores morosos, Contraloría, Registraduría, Sistema Registro Nacional, Inhabilidades Policía), que aún no existen en el PDF de 8 páginas. Los campos de la portada (nombre, identificación, fecha de consulta) siguen con "xxxx" sin rellenar.

## Decisiones y hallazgos importantes
- **No saltarse controles anti-bot.** Cloudflare (FBI, Consilium) y el captcha del RUNT los pasa una persona. Con Playwright el FBI devuelve 403 "Just a moment..." y la verificación entra en bucle.
- **FBI:** el filtro a veces no se aplica y la página queda en `Results: 440 Items` (lista sin filtrar), aunque se espere mucho; es un problema del sitio/Cloudflare y de las pruebas repetidas, no de tiempos. Hay que revisar que `Results:` sea coherente. Con un nombre sin coincidencias el sitio también muestra la lista completa.
- El navegador por defecto del usuario es Edge. Las capturas de pantalla muestran cualquier ventana encima (por ejemplo un video flotante): hay que cerrarlas.
- Procuraduría bloquea el user agent "HeadlessChrome"; por eso se usa un user agent normal. Los certificados se ignoran (`ignore_https_errors=True`), aprobado por el usuario.
- Un visor de PDF dentro de la página solo pinta lo visible: para la libreta la ventana debe abarcar todo el alto.
- `esperar_sin_carga` (spinners/"Cargando") se usa solo en el RUNT; en SIMIT se espera al texto del resultado porque `networkidle` no llega nunca.
- Los hilos de Playwright usan cada uno su `sync_playwright`.
- Error ya corregido: en `main()` no se debe usar `consultar` como variable de bucle (sombreaba la función de la Procuraduría).

## Entorno y trampas
- El usuario ejecuta con `py main.py` (Python global 3.14). Las dependencias deben instalarse con `py -m pip install -r requirements.txt` (también existe un `.venv`, pero no es lo que usa el usuario).
- La terminal de las herramientas a veces cambia de carpeta o rechaza `&`; usar rutas absolutas y, para scripts largos, escribir los resultados en un archivo y leerlo. Los comandos "sync" a veces devuelven antes de terminar: usar modo asíncrono y consultar la salida.
- La salida de `list_dir` puede quedar en caché; verificar con un comando.
- Sintaxis usada: f-strings con barras invertidas dentro de la expresión (Python 3.12+).

## Cómo trabajar con el usuario
- Pide una página nueva cada vez, con su URL y qué escribir; hay que inspeccionarla, implementarla como `consultar_*`, probarla (dos consultas seguidas, ver la captura y el PDF) y añadirla a `UBICACION_PDF`, `trabajadores` y al envío de tareas en `main()`.
- Para inspeccionar, crea scripts de prueba temporales en la raíz y bórralos al terminar.
- Si el usuario da datos de ejemplo reales, no los dejes en el código ni en archivos que se suban.
- Prefiere cambios mínimos y no modifiques el comportamiento existente sin que lo pida.
