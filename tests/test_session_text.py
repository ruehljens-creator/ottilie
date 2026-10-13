

def test_spurname_ohne_kanalformat():
    """Pro Tools schreibt „ST (Stereo)“ in den Session-Text (live 04.10.2026)."""
    from tonmischung.core.session_text import track_clips, track_name
    assert [track_name(n) for n in ("ST (Stereo)", "A1 (Mono)", "Atmo (5.1)", "Spr (7.1.2)", "Mix (Ambisonics 1st Order)")] \
        == ["ST", "A1", "Atmo", "Spr", "Mix"]
    assert track_name("Interview (2)") == "Interview (2)" and track_name("Interview (Teil 2)") == "Interview (Teil 2)"
    text = ("SESSION NAME:\tX\n\nTRACK NAME:\tST (Stereo)\nCHANNEL \tEVENT   \tCLIP NAME\n"
            "1       \t1       \tMix-01\t   10:00:00:00\t   10:00:20:05\t   00:00:20:05\tUnmuted\n")
    assert track_clips(text) == {"ST": [(900000, 900505, "Mix-01")]}
