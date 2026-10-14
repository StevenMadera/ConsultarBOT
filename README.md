# ConsultarBot

Consulta automática de una persona en varias páginas públicas, con captura de cada resultado y un PDF final.

## Requisitos
- Windows 10/11
- Python 3.12 o superior (el comando `py` debe funcionar)
- Edge o Chrome como navegador por defecto

## Instalación (una sola vez)
```
git clone <URL-del-repositorio>
cd ConsultarBot
py -m pip install -r requirements.txt
py -m playwright install chromium
```

## Uso
```
py main.py
```
1. Pega la fila de Excel con: nombre, documento, empresa, fecha consulta, quien consulta, fecha expedicion (ej. `Santiago Flores Alonso | 1000858557 | FALABELLA CEDI | 16/09/2026 | STEVEN | 25/06/2020`). Si el documento no indica tipo, es CC; tipos: CC, CE, PEP, PPT, NIT, TI (ej. `CE 123456`). A mano, separa los datos con `;` o `,`.
2. Resuelve los captchas cuando la ventana de cada sitio quede al frente, uno tras otro:
   - RUNT y Registraduría: escribe en la consola el código que ves en la ventana.
   - Antecedentes e Inhabilidades (Policía): marca "No soy un robot" en la ventana; el bot sigue solo.
3. Si el FBI o Consilium muestran la verificación de Cloudflare, pásala en el navegador y pulsa Enter.
4. El PDF queda en `salidas/` y las capturas en `capturas/`.
5. Enter sin texto para salir.

## Antes de ejecutar
- Cierra videos flotantes u otras ventanas encima del navegador: salen en la captura del FBI y de Consilium.
- Si falla una página, el error queda en `log.txt` y el bot sigue con las demás.
