import numpy as np
from scipy.spatial import distance


class BEREstimator:
    def __init__(self, x, y, subgroups=None):
        """
        Initialize BEREstimator class instance.

        Parameters
        ----------
            x: numpy array
                Each row should be one data point, and each column represents a
                feature.

            y: numpy array
                This array should have length equal to the number of rows in
                `x`. Each entry should be 0 or 1.

            subgroups: dict(str -> numpy array)
                String keys represent the category of subgroup (e.g., race,
                gender, etc.)
        """
        self.x = x
        self.y = y
        self.subgroups = subgroups

    def mahalanobis_bound(self):
        """
        Calculate the Mahalanobis distance between instances in class 0 and class 1.

        Equations from Tumer and Ghosh (2003) and also referencing Ryan Holbrook's
        implementations at:
        https://rdrr.io/github/ryanholbrook/bayeserror/src/R/bayeserror.R

        """
        p_1 = self.y.mean()
        p_0 = 1 - p_1
        mu_0 = self.x[self.y == 0, :].mean(axis=0)  # mean vector for class 0 instances
        mu_1 = self.x[self.y == 1, :].mean(axis=0)  # mean vector for class 1 instances
        sigma_0 = np.cov(self.x[self.y == 0, :].T)
        sigma_1 = np.cov(self.x[self.y == 1, :].T)
        sigma_inv = np.linalg.inv(sigma_0 * p_0 + sigma_1 * p_1)
        m_dist = distance.mahalanobis(mu_0, mu_1, sigma_inv) ** 2
        return 2 * p_0 * p_1 / (1 + p_0 * p_1 * m_dist)


