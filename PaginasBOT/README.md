# PaginasBOT

Consulta automática de una persona en varias páginas públicas, con captura de cada resultado y un PDF final.

## Requisitos
- Windows 10/11
- Python 3.12 o superior (el comando `py` debe funcionar)
- Edge o Chrome como navegador por defecto

## Instalación (una sola vez por equipo)
Doble clic en `Instalar.bat`, en la carpeta que contiene PaginasBOT, AntecedentesBOT y CuadrosBOT: instala las librerías y Chromium y crea los accesos directos (con su ícono) en el Escritorio y en el menú Inicio. Si se mueve la carpeta, se vuelve a ejecutar.

Sin el instalador:
```
py -m pip install -r requirements.txt
py -m playwright install chromium
```

## Uso
Doble clic en el acceso directo **PaginasBOT** (o en `PaginasBOT.pyw`). Se abre una ventanita en el centro de la pantalla, siempre encima de las demás.

1. Copia la fila en Excel: nombre, documento, empresa, fecha consulta, quien consulta, fecha expedicion, sexo (ej. `Santiago Flores Alonso | 1000858557 | FALABELLA CEDI | 16/09/2026 | STEVEN | 25/06/2020 | H`). Los datos se llenan solos (también con Ctrl+V en cualquier casilla, o escribiéndolos). Pulsa Enter o «Consultar». Sexo: `H` (hombre) o `M` (mujer); a las mujeres no se les consulta la libreta militar y su informe usa `Plantilla/Mujer.pdf`. Si el documento no indica tipo, es CC; tipos: CC, CE, PEP, PPT, NIT, TI (ej. `CE 123456`).
2. La ventanita muestra el avance de cada sitio. Cuando te necesita, suena un aviso:
   - RUNT y Registraduría: la imagen del código aparece en la ventanita; escríbelo y pulsa Enter.
   - Antecedentes e Inhabilidades (Policía): la ventanita se pasa a la esquina superior derecha; marca "No soy un robot" en la ventana del navegador y el bot sigue solo.
   - FBI o Consilium (verificación de Cloudflare o cookies): haz el paso en el navegador y pulsa «Listo».
   - SIMIT con varias personas: elige la que corresponde.

   Al empezar se abren minimizadas dos ventanas de Chrome (FBI y Consilium): no las cierres, el bot las captura y las cierra al final.
3. Al terminar: «Abrir PDF», «Abrir carpeta» y lo que no se pudo consultar. Para otra persona, copia la siguiente fila. El PDF queda en `salidas/` y las capturas en `capturas/`.
4. Cierra la ventanita para salir; si hay una consulta en curso, pregunta antes.

Sin ventanita, en la consola: `py main.py` (los códigos se escriben en la consola y Enter sin texto sale).

## Antes de ejecutar
- Cierra videos flotantes u otras ventanas encima del navegador: salen en la captura del FBI y de Consilium. La ventanita de PaginasBOT no sale (Windows la excluye de las capturas de pantalla; tampoco aparece en tus recortes ni al compartir pantalla).
- Si falla una página, el error queda en `log.txt` y el bot sigue con las demás.
