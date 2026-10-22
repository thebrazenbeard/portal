class CognitionBoundaryError(RuntimeError):
    pass
class InferencePermit:
    def __init__(self, *, model, digest, scope):
        self.model, self.digest, self.scope = model, digest, scope
class LocalCognitionGate:
    def __init__(self, *, model, expected_digest, port=11434, opener=None, armed=False):
        self.model, self.expected_digest, self.port, self.opener, self.armed = model, expected_digest, port, opener, armed
    def generate(self, prompt, *, permit=None, max_tokens=64):
        raise NotImplementedError("local cognition boundary not implemented")
