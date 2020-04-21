"""
Copyright (C) Microsoft Corporation. All rights reserved.​
 ​
Microsoft Corporation (“Microsoft”) grants you a nonexclusive, perpetual,
royalty-free right to use, copy, and modify the software code provided by us
("Software Code"). You may not sublicense the Software Code or any use of it
(except to your affiliates and to vendors to perform work on your behalf)
through distribution, network access, service agreement, lease, rental, or
otherwise. This license does not purport to express any claim of ownership over
data you may have shared with Microsoft in the creation of the Software Code.
Unless applicable law gives you more rights, Microsoft reserves all other
rights not expressly granted herein, whether by implication, estoppel or
otherwise. ​
 ​
THE SOFTWARE CODE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
MICROSOFT OR ITS LICENSORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR
BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER
IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THE SOFTWARE CODE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.
"""

import pandas as pd
import numpy as np

from msanomalydetector.util import *
import msanomalydetector.boundary_utils as boundary_helper
from msanomalydetector._anomaly_kernel_cython import median_filter


class SpectralResidual:
    def __init__(self, series, threshold, mag_window, score_window, sensitivity, detect_mode):
        self.__series__ = series
        self.__values__ = self.__series__['value'].tolist()
        self.__threshold__ = threshold
        self.__mag_window = mag_window
        self.__score_window = score_window
        self.__sensitivity = sensitivity
        self.__detect_mode = detect_mode
        self.__anomaly_frame = None

    def detect(self):
        if self.__anomaly_frame is None:
            self.__anomaly_frame = self.__detect()

        return self.__anomaly_frame

        return anomaly_frame

    def __detect(self):
        extended_series = SpectralResidual.extend_series(self.__values__)
        mags = self.spectral_residual_transform(extended_series)[:len(self.__series__)]
        anomaly_scores = self.generate_spectral_score(mags)
        anomaly_frame = pd.DataFrame({Timestamp: self.__series__['timestamp'],
                                      Value: self.__series__['value'],
                                      AnomalyId: list(range(0, len(anomaly_scores))),
                                      Mag: mags,
                                      AnomalyScore: anomaly_scores})
        anomaly_frame[IsAnomaly] = np.where(anomaly_frame[AnomalyScore] >= self.__threshold__, True, False)
        anomaly_frame.set_index(AnomalyId, inplace=True)

        if self.__detect_mode == DetectMode.anomaly_and_margin:
            anomaly_frame[ExpectedValue] = self.calculate_expected_value(anomaly_frame[anomaly_frame[IsAnomaly]].index.tolist())
            boundary_units = boundary_helper.calculate_bounary_unit_entire(np.asarray(self.__values__),
                                                                           anomaly_frame[IsAnomaly].values)
            anomaly_frame[AnomalyScore] = boundary_helper.calculate_anomaly_scores(
                values=anomaly_frame[Value].values,
                expected_values=anomaly_frame[ExpectedValue].values,
                units=boundary_units,
                is_anomaly=anomaly_frame[IsAnomaly].values
            )

            margins = [boundary_helper.calculate_margin(u, self.__sensitivity) for u in boundary_units]

            anomaly_frame[LowerBoundary] = anomaly_frame[ExpectedValue].values - margins
            anomaly_frame[UpperBoundary] = anomaly_frame[ExpectedValue].values + margins
            anomaly_frame[IsAnomaly] = np.logical_and(anomaly_frame[IsAnomaly].values,
                                                      anomaly_frame[LowerBoundary].values <= anomaly_frame[Value].values)
            anomaly_frame[IsAnomaly] = np.logical_and(anomaly_frame[IsAnomaly].values,
                                                      anomaly_frame[Value].values <= anomaly_frame[UpperBoundary].values)

        return anomaly_frame

    def generate_spectral_score(self, mags):
        ave_mag = average_filter(mags, n=self.__score_window)
        ave_mag[np.where(ave_mag <= EPS)] = EPS

        raw_scores = abs(mags - ave_mag) / ave_mag
        scores = np.clip(raw_scores / 10.0, 0, 1.0)

        return scores

    def spectral_residual_transform(self, values):
        """
        This method transform a time series into spectral residual series
        :param values: list.
            a list of float values.
        :return: mag: list.
            a list of float values as the spectral residual values
        """

        trans = np.fft.fft(values)
        mag = np.sqrt(trans.real ** 2 + trans.imag ** 2)
        eps_index = np.where(mag <= EPS)[0]
        mag[eps_index] = EPS

        mag_log = np.log(mag)
        mag_log[eps_index] = 0

        spectral = np.exp(mag_log - average_filter(mag_log, n=self.__mag_window))

        trans.real = trans.real * spectral / mag
        trans.imag = trans.imag * spectral / mag
        trans.real[eps_index] = 0
        trans.imag[eps_index] = 0

        wave_r = np.fft.ifft(trans)
        mag = np.sqrt(wave_r.real ** 2 + wave_r.imag ** 2)
        return mag

    @staticmethod
    def predict_next(values):
        """
        Predicts the next value by sum up the slope of the last value with previous values.
        Mathematically, g = 1/m * sum_{i=1}^{m} g(x_n, x_{n-i}), x_{n+1} = x_{n-m+1} + g * m,
        where g(x_i,x_j) = (x_i - x_j) / (i - j)
        :param values: list.
            a list of float numbers.
        :return : float.
            the predicted next value.
        """

        if len(values) <= 1:
            raise ValueError(f'data should contain at least 2 numbers')

        v_last = values[-1]
        n = len(values)

        slopes = [(v_last - v) / (n - 1 - i) for i, v in enumerate(values[:-1])]

        return values[1] + sum(slopes)

    @staticmethod
    def extend_series(values, extend_num=5, look_ahead=5):
        """
        extend the array data by the predicted next value
        :param values: list.
            a list of float numbers.
        :param extend_num: int, default 5.
            number of values added to the back of data.
        :param look_ahead: int, default 5.
            number of previous values used in prediction.
        :return: list.
            The result array.
        """

        if look_ahead < 1:
            raise ValueError('look_ahead must be at least 1')

        extension = [SpectralResidual.predict_next(values[-look_ahead - 2:-1])] * extend_num
        return values + extension

    def calculate_expected_value(self, anomaly_index):
        values = deanomaly_entire(self.__values__, anomaly_index)
        length = len(values)
        fft_coef = np.fft.fft(values)
        fft_coef.real = [v if length * 3 / 8 >= i or i >= length * 5 / 8 else 0 for i, v in enumerate(fft_coef.real)]
        fft_coef.imag = [v if length * 3 / 8 >= i or i >= length * 5 / 8 else 0 for i, v in enumerate(fft_coef.imag)]
        exps = np.fft.ifft(fft_coef)
        return exps
