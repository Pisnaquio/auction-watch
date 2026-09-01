# Cómo buscar mejor

Auction Watch funciona mejor si empezás con una búsqueda amplia, ejecutás una
corrida y ajustás los criterios de a poco. La aplicación nunca modifica tus
criterios automáticamente.

## Los campos principales

### Cualquiera de estos términos

Amplía resultados: alcanza con que aparezca uno. Para una mesa de pool, por
ejemplo: `mesa de pool, billar, pool`.

### Debe incluir todos

Restringe mucho: cada resultado debe contener todos los términos. Un ejemplo
útil es `mesa, ping pong`. Evitá mezclar conceptos sin relación: `autor, tapa
dura` no ayuda a encontrar consolas.

Si escribís tres o más términos separados por comas y dejás “Cualquiera” vacío,
la interfaz te sugerirá mover algunos. Es sólo una advertencia: podés guardar
igual y Auction Watch nunca cambia los criterios por su cuenta.

### Frases exactas y exclusiones

Usá frases exactas para nombres o modelos concretos, como `game boy color` o el
nombre completo de un álbum. Las exclusiones evitan falsos positivos: `réplica,
funda, lámina, roto`.

### Fuentes

- **Bavastro:** remates y lotes de su catálogo público.
- **Castells:** remates descubiertos en su sitio y lotes de su API pública.
- **Prado:** productos públicos identificados como remates.
- **Remotes:** remates y lotes visibles en su feed público.
- **TodoRemates:** categorías y publicaciones de su catálogo público.

Conviene comenzar con varias fuentes activas porque cada rematador publica
inventario distinto. Una fuente parcial no oculta los resultados sanos de las
demás.

En Castells, los remates cuyo título es inequívocamente de pinacoteca, pinturas,
arte, esculturas, acuarelas, grabados, dibujos o litografías se descartan antes
de consultar lotes.
La decisión queda visible como `skipped_irrelevant`. Títulos mixtos —por
ejemplo, arte junto con consolas, muebles, libros o “varios”— y títulos ambiguos
como “Colección particular” se siguen consultando. Un descarte artístico nunca
convierte en completa una corrida que tenga fallos en grupos relevantes.

Si Castells cambia el envelope JSON, Auction Watch busca listas de lotes con un
recorrido limitado por profundidad y cantidad de nodos. Sólo recupera en la
misma corrida una lista única donde la gran mayoría de las filas tenga identidad
y título estables. Si aparecen varias listas, faltan campos, llega HTML, un
objeto de error o un vacío no verificable, el resultado queda `partial` y el
candidato se evalúa únicamente en sombra.

El snapshot conserva un fingerprint estructural con rutas y nombres de campos,
nunca valores del payload. Ese diagnóstico no genera solicitudes adicionales,
no persiste lotes, no modifica inventario y no dispara notificaciones por sí
solo.

Castells tampoco toma una desaparición del listado, un grupo activo vacío o
una caída repentina de más del 75% como prueba suficiente para borrar lotes ya
vistos. Conserva el último inventario sano y muestra la fuente como `partial`.
Su consulta tiene un máximo de 8 segundos por solicitud y 60 segundos en total;
al alcanzar esos límites publica las demás fuentes y la cobertura válida que
ya obtuvo. No hay reintentos ni consultas adicionales de drift en segundo
plano.

### Categorías, precio, urgencia y frecuencia

Las categorías y el precio máximo reducen ruido. El editor actual aplica un
máximo; todavía no ofrece un mínimo editable, por lo que esa limitación debe
considerarse al revisar resultados. Para una búsqueda urgente usá más horarios
y notificaciones. Para una búsqueda tranquila, menos frecuencia.

La automatización de cada perfil se controla con un interruptor independiente.
Al activarla, indicá uno o más horarios `HH:MM` y una zona IANA, por ejemplo
`America/Montevideo`. La cabecera muestra si el worker y el scheduler global del
entorno están realmente activos. Una corrida manual `completed` o `partial` con
snapshot, hecha hasta 15 minutos antes del horario o durante su ventana de
ejecución, cubre ese slot y evita repetirla. El scheduler sólo recupera un
horario durante los 15 minutos siguientes: un reinicio varias horas más tarde
no dispara una corrida inesperada.

## Qué significa la cobertura

- **complete:** la fuente verificó toda la cobertura prevista. Cero hallazgos
  puede interpretarse dentro de los criterios elegidos.
- **partial:** hay resultados válidos, pero falta una parte verificable. Conservá
  el snapshot y revisá la causa antes de cerrar más la búsqueda.
- **failed:** la fuente no entregó cobertura publicable. No interpretes cero
  como ausencia y mantené otras fuentes activas.

“Sin hallazgos” no demuestra que no existan remates: los criterios pueden ser
demasiado cerrados, quizá no haya inventario relevante o una fuente puede haber
quedado parcial.

## Recetas iniciales

- **Consolas:** cualquiera `consola, playstation, nintendo, sega, xbox`; frases
  `game boy, family game`; excluir `funda, lámina, libro`.
- **Discos:** cualquiera `vinilo, disco, LP`; frase con artista o álbum; excluir
  `decorativo, reloj`.
- **Libros:** cualquiera `libro, novela, colección`; frase con autor o título;
  excluir `revista, fotocopia`.
- **Mesa de pool:** cualquiera `mesa de pool, billar, pool`; excluir `miniatura,
  juguete`.
- **Mesa de ping pong:** cualquiera `ping pong, tenis de mesa`, o todos `mesa,
  ping pong`; excluir `paleta, pelota, red`.

## Flujo recomendado

1. Crear un perfil.
2. Elegir varias fuentes.
3. Comenzar con pocos términos en “Cualquiera”.
4. Ejecutar una corrida.
5. Ajustar exclusiones, categorías y precio.
6. Activar frecuencia y notificaciones cuando el resultado sea útil.
