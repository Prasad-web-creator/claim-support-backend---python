import io, os, sys
_worker_paddle = None
def init_worker():
    global _worker_paddle
    os.environ['FLAGS_logtostderr'] = '0'
    os.environ['GLOG_v'] = '0'
    os.environ['PADDLE_LOG_LEVEL'] = 'ERROR'
    pid = os.getpid()
    try:
        from paddleocr import PaddleOCR
        _worker_paddle = PaddleOCR(use_angle_cls=False, lang='en', show_log=False, cpu_threads=2, det_db_score_mode='fast')
        print('[OCR Worker PID=' + str(pid) + '] PaddleOCR ready', flush=True)
    except Exception as e:
        print('[OCR Worker PID=' + str(pid) + '] Init FAILED: ' + str(e), file=sys.stderr)
        _worker_paddle = None
def ocr_page(args):
    global _worker_paddle
    page_idx, image_bytes = args
    if _worker_paddle is None:
        init_worker()
    if _worker_paddle is None:
        return page_idx, ''
    try:
        import numpy as np
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        result = _worker_paddle.ocr(np.array(img), cls=False)
        if not result or not result[0]:
            return page_idx, ''
        lines = []
        for box in result[0]:
            if box and len(box) > 1:
                rec = box[1]
                txt = rec[0] if isinstance(rec, (list, tuple)) else str(rec)
                if txt and txt.strip():
                    lines.append(txt.strip())
        return page_idx, '\n'.join(lines)
    except Exception as e:
        print('[OCR Worker PID=' + str(os.getpid()) + '] Page ' + str(page_idx+1) + ' err: ' + str(e), file=sys.stderr)
        return page_idx, ''
