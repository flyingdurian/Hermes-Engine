extends Node
## Hermes Engine V3: WAV-first, single-voice Godot 4.x adapter.
##
## Scene children:
##   HermesTTS (Node; attach this script)
##   ├── HTTPRequest
##   └── VoicePlayer (AudioStreamPlayer)
##
## Not yet validated inside The Last Emporium or on the owner's Mac.
## This helper deliberately does NOT implement PCM /stream or retry/queue.

signal speech_started(npc_id: String)
signal speech_finished(npc_id: String)
signal speech_failed(npc_id: String, reason: String)

@export var base_url: String = "http://10.0.0.129:8000"
@export_range(5.0, 180.0, 1.0) var request_timeout_s: float = 90.0

@onready var _http: HTTPRequest = $HTTPRequest
@onready var _player: AudioStreamPlayer = $VoicePlayer

var _request_pending: bool = false
var _current_npc_id: String = ""


func _ready() -> void:
	_http.timeout = request_timeout_s
	_http.request_completed.connect(_on_request_completed)
	_player.finished.connect(_on_playback_finished)


func is_busy() -> bool:
	return _request_pending or _player.playing


func speak_line(
	npc_id: String,
	dialogue: String,
	voice_id: String,
	mood: String = "neutral",
	acting_prompt: String = "",
	engine: String = "qwen",
	archetype: String = "female_warm"
) -> bool:
	# Game owns npc_id -> voice_id mapping. Do not send npc_id:
	# the server's Pydantic model forbids unknown request fields.
	if is_busy() or dialogue.strip_edges().is_empty():
		return false
	if not engine in ["qwen", "chatterbox", "kokoro"]:
		speech_failed.emit(npc_id, "Unsupported Hermes engine")
		return false

	var data: Dictionary = {
		"engine": engine,
		"input": dialogue,
		"voice": voice_id,
		"mood": mood,
		"instruct": acting_prompt,
		"archetype": archetype,
		"speed": 1.0,
		"pitch_shift": 0.0
	}
	var url: String = base_url.strip_edges()
	if url.ends_with("/"):
		url = url.substr(0, url.length() - 1)
	_current_npc_id = npc_id
	_request_pending = true
	var error: Error = _http.request(
		url + "/v1/audio/speech",
		PackedStringArray(["Content-Type: application/json"]),
		HTTPClient.METHOD_POST,
		JSON.stringify(data)
	)
	if error != OK:
		_request_pending = false
		_current_npc_id = ""
		speech_failed.emit(npc_id, "Could not start HTTP request: %s" % error_string(error))
		return false
	return true


func stop_line() -> void:
	# This stops the Godot client's download/audio.
	# It does NOT guarantee immediate cancellation of MLX inference.
	if _request_pending:
		_http.cancel_request()
	_request_pending = false
	_player.stop()
	_current_npc_id = ""


func _on_request_completed(
	result: int,
	response_code: int,
	_headers: PackedStringArray,
	body: PackedByteArray
) -> void:
	if not _request_pending:
		return  # Client cancelled; ignore late network result.
	_request_pending = false
	var npc_id: String = _current_npc_id

	if result != HTTPRequest.RESULT_SUCCESS or response_code != 200:
		var detail: String = "HTTP %d / transport %d" % [response_code, result]
		var parsed: Variant = JSON.parse_string(body.get_string_from_utf8())
		if parsed is Dictionary:
			detail += ": " + str(parsed.get("detail", "unknown error"))
		_current_npc_id = ""
		speech_failed.emit(npc_id, detail)
		return

	var sound: AudioStreamWAV = _parse_mono_pcm16_wav(body)
	if sound == null:
		_current_npc_id = ""
		speech_failed.emit(npc_id, "Unsupported/invalid WAV; expected mono PCM16 RIFF/WAVE")
		return

	_player.stream = sound
	_player.play()
	speech_started.emit(npc_id)


func _on_playback_finished() -> void:
	var npc_id: String = _current_npc_id
	_current_npc_id = ""
	if not npc_id.is_empty():
		speech_finished.emit(npc_id)


func _parse_mono_pcm16_wav(bytes: PackedByteArray) -> AudioStreamWAV:
	# Portable Godot 4.x implementation; Godot 4.4+ also has
	# AudioStreamWAV.load_from_buffer(bytes). V3's soundfile WAV
	# endpoint normally produces PCM16 mono.
	if bytes.size() < 44:
		return null
	if bytes.slice(0, 4).get_string_from_ascii() != "RIFF":
		return null
	if bytes.slice(8, 12).get_string_from_ascii() != "WAVE":
		return null

	var pos: int = 12
	var encoding: int = -1
	var channels: int = 0
	var sample_rate: int = 0
	var bits_per_sample: int = 0
	var audio_bytes: PackedByteArray = PackedByteArray()
	var found_fmt: bool = false
	var found_data: bool = false

	while pos + 8 <= bytes.size():
		var chunk_id: String = bytes.slice(pos, pos + 4).get_string_from_ascii()
		var chunk_size: int = bytes.decode_u32(pos + 4)
		var content_pos: int = pos + 8
		if chunk_size < 0 or content_pos + chunk_size > bytes.size():
			return null

		if chunk_id == "fmt ":
			if chunk_size < 16:
				return null
			encoding = bytes.decode_u16(content_pos)
			channels = bytes.decode_u16(content_pos + 2)
			sample_rate = bytes.decode_u32(content_pos + 4)
			bits_per_sample = bytes.decode_u16(content_pos + 14)
			found_fmt = true
		elif chunk_id == "data":
			audio_bytes = bytes.slice(content_pos, content_pos + chunk_size)
			found_data = true

		pos = content_pos + chunk_size + (chunk_size % 2)

	if not found_fmt or not found_data:
		return null
	if encoding != 1 or channels != 1 or bits_per_sample != 16:
		return null
	if sample_rate <= 0 or audio_bytes.is_empty() or audio_bytes.size() % 2 != 0:
		return null

	var sound: AudioStreamWAV = AudioStreamWAV.new()
	sound.format = AudioStreamWAV.FORMAT_16_BITS
	sound.stereo = false
	sound.mix_rate = sample_rate
	sound.data = audio_bytes
	return sound
