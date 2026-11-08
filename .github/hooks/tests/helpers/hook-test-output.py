import unittest


class HookTestRunner(unittest.TextTestRunner):
    def run(self, test):
        result = super().run(test)
        if not result.wasSuccessful():
            self.stream.writeln('NG')
        return result


def run_tests():
    unittest.main(testRunner=HookTestRunner)
