TEST_FRAME_SEQUENCES = [
    # Ten frames from trimmed video, one of the noses disappears for 1 frame.
    b"""
    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
    INW68QAAHyCkVQ==
    """,
    # Another 3 frame section from the V2Trim.mp4 video. Contains another point loss.
    b"""
    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
    uLS7xz78+d69URfYD4Smhxz44qy23+Vck4O0xnKH/hJ9h8i/Fvv8ucUO2FnaHdjtnXBg6k5rewCv
    bsb8A64BAAAAAAAAeJyTZYAAViBmg2J2KOYAYgkglgRiaSCWQcKySFgViNWwYHUCmBuIudBofiAW
    hWJJJPul0NjqSPaC2BpArAn1iwoOOS2Q5I9rlgYmtbZCZyvt7hSdtFXIK7VhYMgwY2BYYcrAcAeI
    dcwZHmRa/Ptfb99yv96eoUHO3O1vvb3V7UR7sJoETQuGC10WDAIPLRgOtJgzNADVN2iZMzDMMWUw
    ULRkuGFjyRCUY8WwYJsFQ8N/oLiTGYfdZodakUv21jGd9vu29B9QDd11YPLmjQ6vXk12kNvOdeDc
    PxlH79emDimfXR1kV2k4CNVOPLBv+YIDR6XnH4h4Lea4y3+Nw6UHtx02GD5y+Gq80qFs4VKHG4Vl
    DlWTdzm0uF11kJK/73D8CZNjyOk7Dgvbdju0OUxzmLNXYL/PglkOzzX1DhSa6ztkbSk+4CwYar/a
    VWc/d4zIvs9rn9vrX5d2rPn3077Zd/eB+v3CjlXWn+yjCnYc2My6yf6E6kGHprdn7LdE37dT27V5
    P3sg64GfsxccUFI1dvjWct5hmbOJQ7Q70D9Tb+9XOntnv4BwzQEAQmub9g==
    """,
    # A 10 frame from V2Trim.mp4, this one contain a point that is lost twice.
    b"""
    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
    DpYcE+ynbjy9fwH/5f1h83MPAABLj7Rk
    """
    # TODO: Add more...
]


def print_test_set():
    from diplomat.utils import extract_frames

    for i, s in enumerate(TEST_FRAME_SEQUENCES):
        print(f"Test Frame Sequence {i}: ")
        for j, frm in enumerate(extract_frames.unpack_frame_string(s, 1)[1]):
            for bp_idx in range(frm.get_bodypart_count()):
                print(f"Frame {j} Body Part {bp_idx}")
                extract_frames.pretty_print_frame(frm, 0, bp_idx)


if(__name__ == "__main__"):
    print_test_set()
