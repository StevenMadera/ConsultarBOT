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
1. Pega los datos: `TIPO NUMERO NOMBRE COMPLETO` (ej. `CC 1000000000 Juan Perez Gomez`). Tipos: CC, CE, PEP, PPT, NIT, TI.
2. Escribe el captcha del RUNT cuando lo pida (se ve en la ventana del RUNT).
3. Si el FBI o Consilium muestran la verificación de Cloudflare, pásala en el navegador y pulsa Enter.
4. El PDF queda en `salidas/` y las capturas en `capturas/`.
5. Enter sin texto para salir.

## Antes de ejecutar
- Cierra videos flotantes u otras ventanas encima del navegador: salen en la captura del FBI y de Consilium.
- Si falla una página, el error queda en `log.txt` y el bot sigue con las demás.
