package ru.audiotext.studio

import android.content.Context
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import kotlin.math.abs
import kotlin.random.Random

internal data class Cell(val x: Int, val y: Int)
internal enum class Direction(val dx: Int, val dy: Int) {
    UP(0, -1), DOWN(0, 1), LEFT(-1, 0), RIGHT(1, 0)
}
internal data class SnakeState(
    val body: List<Cell>, val food: Cell?, val direction: Direction,
    val nextDirection: Direction, val score: Int = 0, val finished: Boolean = false
)
internal object SnakeRules {
    const val SIDE = 14
    fun newGame(random: Random = Random.Default): SnakeState {
        val start = listOf(Cell(6, 7), Cell(5, 7), Cell(4, 7))
        return SnakeState(start, placeFood(start, random), Direction.RIGHT, Direction.RIGHT)
    }
    fun turn(s: SnakeState, d: Direction): SnakeState {
        if (s.finished || d.dx == -s.direction.dx && d.dy == -s.direction.dy) return s
        return s.copy(nextDirection = d)
    }
    fun move(s: SnakeState, random: Random = Random.Default): SnakeState {
        if (s.finished) return s
        val h = s.body.first()
        val next = Cell(h.x + s.nextDirection.dx, h.y + s.nextDirection.dy)
        val eat = next == s.food
        val occupied = if (eat) s.body else s.body.dropLast(1)
        if (next.x !in 0 until SIDE || next.y !in 0 until SIDE || next in occupied) {
            return s.copy(finished = true, direction = s.nextDirection)
        }
        val newBody = listOf(next) + if (eat) s.body else s.body.dropLast(1)
        val food = if (eat) placeFood(newBody, random) else s.food
        return SnakeState(newBody, food, s.nextDirection, s.nextDirection,
            s.score + if (eat) 10 else 0, eat && food == null)
    }
    private fun placeFood(body: List<Cell>, random: Random): Cell? {
        val occupied = body.toHashSet()
        val free = ArrayList<Cell>()
        for (y in 0 until SIDE) for (x in 0 until SIDE) {
            val c = Cell(x, y)
            if (c !in occupied) free.add(c)
        }
        return if (free.isEmpty()) null else free[random.nextInt(free.size)]
    }
}

internal object SnakeLeaderboard {
    private const val NAME = "audiotext_snake"
    private const val KEY = "scores"
    fun read(context: Context): List<Pair<Int, Long>> =
        context.getSharedPreferences(NAME, Context.MODE_PRIVATE).getString(KEY, "").orEmpty()
            .split(";").mapNotNull {
                val values = it.split(":")
                val points = values.getOrNull(0)?.toIntOrNull()
                val millis = values.getOrNull(1)?.toLongOrNull()
                if (points != null && millis != null && points > 0) points to millis else null
            }.sortedByDescending { it.first }.take(10)

    fun save(context: Context, points: Int): List<Pair<Int, Long>> {
        if (points <= 0) return read(context)
        val list = (read(context) + (points to System.currentTimeMillis()))
            .sortedWith(compareByDescending<Pair<Int, Long>> { it.first }.thenBy { it.second }).take(10)
        context.getSharedPreferences(NAME, Context.MODE_PRIVATE).edit()
            .putString(KEY, list.joinToString(";") {
                it.first.toString() + ":" + it.second.toString()
            }).apply()
        return list
    }
}

/** Runs only while visible, on one low-frequency coroutine (no background game worker). */
@Composable
internal fun WaitingSnake(context: Context) {
    var game by remember { mutableStateOf(SnakeRules.newGame()) }
    var playing by remember { mutableStateOf(true) }
    var saved by remember { mutableStateOf(false) }
    var showScores by remember { mutableStateOf(false) }
    var leaderboard by remember { mutableStateOf(SnakeLeaderboard.read(context)) }

    LaunchedEffect(playing, game.finished) {
        while (playing && !game.finished) {
            delay((300 - game.score / 50 * 10).coerceAtLeast(180).toLong())
            game = SnakeRules.move(game)
        }
    }
    LaunchedEffect(game.finished) {
        if (game.finished && !saved) {
            leaderboard = SnakeLeaderboard.save(context, game.score)
            saved = true
        }
    }
    fun direction(d: Direction) { game = SnakeRules.turn(game, d) }
    fun restart() {
        game = SnakeRules.newGame()
        saved = false
        playing = true
    }
    ElevatedCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text("Змейка", style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                Text(game.score.toString() + " очков", color = MaterialTheme.colorScheme.primary,
                    fontWeight = FontWeight.Bold)
            }
            Text("Играй, пока Whisper обрабатывает запись. Рекорды остаются на телефоне.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Canvas(
                Modifier.fillMaxWidth().aspectRatio(1f)
                    .background(Color(0xFF17233C), RoundedCornerShape(16.dp))
                    .pointerInput(Unit) {
                        var totalX = 0f
                        var totalY = 0f
                        detectDragGestures(onDragEnd = {
                            if (abs(totalX) + abs(totalY) > 28f) {
                                direction(if (abs(totalX) > abs(totalY)) {
                                    if (totalX > 0) Direction.RIGHT else Direction.LEFT
                                } else if (totalY > 0) Direction.DOWN else Direction.UP)
                            }
                            totalX = 0f
                            totalY = 0f
                        }) { change, drag ->
                            change.consume()
                            totalX += drag.x
                            totalY += drag.y
                        }
                    }
            ) {
                val tile = size.minDimension / SnakeRules.SIDE
                for (i in 0..SnakeRules.SIDE) {
                    val at = i * tile
                    drawLine(Color(0xFF29334B), Offset(at, 0f), Offset(at, size.height))
                    drawLine(Color(0xFF29334B), Offset(0f, at), Offset(size.width, at))
                }
                game.food?.let {
                    drawCircle(Color(0xFFFF8E76), tile * 0.32f,
                        Offset((it.x + .5f) * tile, (it.y + .5f) * tile))
                }
                game.body.forEachIndexed { i, cell ->
                    drawRoundRect(
                        if (i == 0) Color(0xFFB5F48A) else Color(0xFF6CD3AD),
                        Offset(cell.x * tile + tile * .08f, cell.y * tile + tile * .08f),
                        Size(tile * .84f, tile * .84f),
                        CornerRadius(tile * .25f)
                    )
                }
            }
            if (game.finished) Text("Конец игры! Можно начать заново.", fontWeight = FontWeight.SemiBold)
            Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                OutlinedButton(onClick = { direction(Direction.UP) }, enabled = !game.finished) {
                    Text("↑", style = MaterialTheme.typography.titleLarge)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { direction(Direction.LEFT) }, enabled = !game.finished) {
                        Text("←", style = MaterialTheme.typography.titleLarge)
                    }
                    OutlinedButton(onClick = { direction(Direction.DOWN) }, enabled = !game.finished) {
                        Text("↓", style = MaterialTheme.typography.titleLarge)
                    }
                    OutlinedButton(onClick = { direction(Direction.RIGHT) }, enabled = !game.finished) {
                        Text("→", style = MaterialTheme.typography.titleLarge)
                    }
                }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(
                    onClick = { if (game.finished) restart() else playing = !playing },
                    modifier = Modifier.weight(1f)
                ) { Text(if (game.finished) "Заново" else if (playing) "Пауза" else "Играть") }
                OutlinedButton(
                    onClick = { showScores = !showScores }, modifier = Modifier.weight(1f)
                ) { Text(if (showScores) "Скрыть рекорды" else "Рекорды") }
            }
            if (showScores) {
                Text("Топ-10 на этом телефоне", fontWeight = FontWeight.SemiBold)
                if (leaderboard.isEmpty()) Text("Пока нет рекордов")
                leaderboard.forEachIndexed { i, score ->
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text((i + 1).toString() + ". " + score.first.toString() + " очков")
                        Text(java.text.SimpleDateFormat("dd.MM.yy", java.util.Locale.getDefault())
                            .format(java.util.Date(score.second)))
                    }
                }
            }
        }
    }
}
