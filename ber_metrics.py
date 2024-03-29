import faiss
import numpy as np
from scipy.spatial import distance, KDTree
from pyitlib import discrete_random_variable as drv


def remove_constant_cols(x):
    """
    Removes columns in the dataset that are the same for each row (i.e.,
    add no information).
    """
    std = x.std(axis=0)
    return x[:, std > 0]

def log_det_svd(m, perc_energy=0.9):
    """
    Calculates the log of the determinant. Uses SVD to remove singular
    values that are "irrelevant"; that is, we keep enough singular
    values to make up 90% of the energy in sigma.

    ** NOT CURRENTLY USED **

    Parameters
    ----------
        m: numpy array
            Matrix whose determinant is being calculated

        eps: float (optional)
            Singular values under this value will be removed
    """
    s = np.linalg.svd(m)[1]
    s_sq = np.power(s, 2)
    cumulative_energy = np.cumsum(s_sq)
    # keep indices with
    num_to_keep = (cumulative_energy <= perc_energy * s_sq.sum()).sum() + 1
    return np.log(np.prod(s[:num_to_keep]))

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
        self.x = remove_constant_cols(x)
        self.y = y
        mu = self.x.mean(axis=0) # mean of each feature
        std = self.x.std(axis=0) # std of each feature
        self.x = (self.x - mu) / std # standardize feature scale, remove features with no variation
        # preprocess y labels to 0 and 1
        possible_labels = np.unique(self.y)
        if len(possible_labels) > 2:
            raise RuntimeError("BER can only be estimated on binary classification tasks. Ensure that there are only two labels.")
        self.y[y == possible_labels[0]] = 0
        self.y[y == possible_labels[1]] = 1
        self.subgroups = subgroups # not currently used

    def mahalanobis_bound(self):
        """
        Calculate the BER upper bound estimate using the Mahalanobis distance between
        instances in class 0 and class 1.

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
        sigma_inv = None
        try:
            sigma_inv = np.linalg.pinv(sigma_0 * p_0 + sigma_1 * p_1)
        except np.linalg.LinAlgError:
            sigma_inv = np.linalg.inv(sigma_0 * p_0 + sigma_1 * p_1)

        m_dist = distance.mahalanobis(mu_0, mu_1, sigma_inv) ** 2
        return 2 * p_0 * p_1 / (1 + p_0 * p_1 * m_dist)

    def bhattacharyya_bound(self, eps=1e-5):
        """
        Calculate the BER upper bound estimate using the Bhattacharrya bound
        between instances in class 0 and class 1.

        Equations from Tumer and Ghosh (2003) and also referencing Ryan Holbrook's
        implementations at:
        https://rdrr.io/github/ryanholbrook/bayeserror/src/R/bayeserror.R

        """
        p_1 = self.y.mean()
        p_0 = 1 - p_1
        x_0 = self.x[self.y == 0]
        x_1 = self.x[self.y == 1]
        no_variation = np.logical_or(x_0.std(axis=0) == 0, x_1.std(axis=0) == 0)
        x_0 = x_0[:, no_variation == False]
        x_1 = x_1[:, no_variation == False]
        assert x_0.shape[1] == x_1.shape[1]  # ensure that the same # of columns
        mu_0 = x_0.mean(axis=0)  # mean vector for class 0 instances
        mu_1 = x_1.mean(axis=0)  # mean vector for class 1 instances
        sigma_0 = np.cov(x_0.T)
        sigma_1 = np.cov(x_1.T)
        sigma = (sigma_0 + sigma_1) / 2
        first_term = (1/8) * (mu_1 - mu_0).T @ sigma @ (mu_1 - mu_0)
        # rewrite to try to escape floating point errors
        second_term = 0.5 * np.linalg.slogdet(sigma)[1] # get the log of absolute value of determinant
        third_term = -0.25 * (np.linalg.slogdet(sigma_0)[1] + np.linalg.slogdet(sigma_1)[1])
        b_dist = first_term + second_term + third_term
        lower_bound = 0.5 * (1 - np.sqrt(1 - 4 * p_0 * p_1 * np.exp(-2 * b_dist)))
        upper_bound = np.exp(-b_dist) * np.sqrt(p_0 * p_1)
        return lower_bound, upper_bound # for now, only interested in upper bound

    def nn_bound(self):
        """
        Calculate the BER upper bound estimate using the nearest neighbor method.
        Currently only supports 0/1 binary class.

        Equations from Tumer and Ghosh (2003) and also referencing Ryan Holbrook's
        implementations at:
        https://rdrr.io/github/ryanholbrook/bayeserror/src/R/bayeserror.R

        Returns lower and upper bound for Bayes error.
        """
        x = np.ascontiguousarray(self.x.astype('float32'))
        index = faiss.IndexFlatL2(x.shape[1])
        index.add(x)
        _, I = index.search(x, k=2)
        closest_idx = I[:, 1].reshape(-1)
        predict_y = self.y[closest_idx]
        upper_bound = (predict_y != self.y).sum() / len(self.y)
        lower_bound = 0.5 * (1 - np.sqrt(1 - 2 * upper_bound))
        return lower_bound, upper_bound

    def mi_ensemble_bound(self, individual_predictions, this_y=None, ensemble_predictions=None):
        """
        Estimate the BER using the Mutual Information-Based Correlation in
        Tumer and Ghosh (2003).

        Parameters
        ----------
            individual_predictions: numpy array
                The dimensions of this array should be |M| by |E|, where
                |M| is the number of labeled data points and |E| is the number
                of individual classifiers. Each element should be a probability
                (not a 0/1 prediction).)
        """
        if this_y is None:
            this_y = self.y
        if ensemble_predictions is None:
            avg_predictor = individual_predictions.mean(axis=1).round()
        else:
            avg_predictor = ensemble_predictions.round()
        individual_predictions = individual_predictions.round() # deal with 0/1 predictions
        N = individual_predictions.shape[1]  # number of classifiers in ensemble
        labels = np.repeat(this_y.reshape(-1, 1), N, axis=1)
        accs = np.equal(individual_predictions, labels).mean(axis=0) # mean accuracy for each classifier
        mean_err = 1 - accs.mean() # mean err for all classifiers
        ensemble_err = 1 - (this_y == avg_predictor).mean() # mean err for ensemble classifier

        # calculate average mutual information between each individual classifier's
        # predictions and the ensemble predictor
        ami = drv.information_mutual(
            individual_predictions.T,
            avg_predictor.reshape(1, -1),
            base=np.e,
            cartesian_product=True
        ).mean()
        # TODO: should we measure total entropy by discretizing the classification
        # probabilities into more granular bins? Currently we just use the
        # 0 / 1 matrix
        # total entropy in the individual classifiers
        total_entropy = drv.entropy_joint(individual_predictions.T, base=np.e)
        # delta is the normalized ami
        delta = ami / total_entropy
        assert delta >= 0
        assert delta <= 1
        # formula from Tumer and Ghosh
        be = (N * ensemble_err - ((N - 1) * delta + 1) * mean_err ) / ((N - 1) * (1 - delta))
        return be

    def plurality_ensemble_bound(self, individual_predictions, this_y=None, lmbda=0.3):
        """
        Estimate the BER using the Plurality Error ensemble-based method in
        Tumer and Ghosh (2003). BEWARE: this is a highly simplistic implementation
        of what is described in the paper. For one, we do not weight by the
        likelihood of the pattern; there's no straightforward way to calculate
        p(x) unless we assume that the features are generated by a
        (class-conditional?) Gaussian.

        Parameters
        ----------
            individual_predictions: numpy array
                The dimensions of this array should be |M| by |E|, where
                |M| is the number of labeled data points and |E| is the number
                of individual classifiers. Each element should be a probability
                (not a 0/1 prediction).)

            lmbda: float
                Float between 0 and 1. Set to 0.3 as per Tumer and Ghosh's paper.
        """
        if this_y is None:
            this_y = self.y
        individual_predictions = individual_predictions.round()
        num_classifiers = individual_predictions.shape[1]
        # fraction of classifiers that "picked" the class 1
        frac_1 = individual_predictions.sum(axis=1) / num_classifiers
        # for which data points does a likely class exist?
        # since this is a binary class problem, if the fraction that voted
        # 1 is greater than (1-lambda) or less than (lambda), then a likely
        # class is 1 or 0, respectively
        likely_class_exists = np.logical_or(frac_1 >= (1 - lmbda), frac_1 <= lmbda)
        # break ties randomly by adding a number between [-0.5, 0.5]
        frac_1[frac_1 == 0.5] += np.random.rand((frac_1 == 0.5).sum()) - 0.5
        vote_correct = frac_1.round() == this_y
        num_likely_exists = likely_class_exists.sum()
        likely_correct = np.logical_and(likely_class_exists, vote_correct)
        return 1 - (likely_correct.sum() / num_likely_exists)

    def bootstrap_ensemble(self, individual_predictions, ensemble_version='mi'): # ensemble_version is 'mi' or 'plurality'
        bers = []
        for i in range(100):
            n = len(individual_predictions)
            samples = np.random.choice(np.arange(n), size=n, replace=True)
            if ensemble_version == 'mi':
                ber = self.mi_ensemble_bound(individual_predictions[samples], this_y=self.y[samples])
            elif ensemble_version == 'plurality':
                ber = self.plurality_ensemble_bound(individual_predictions[samples], self.y[samples])
            else:
                assert NotImplementedError
            bers.append(ber)
        bers = np.sort(bers)
        #return bers
        #return bers[49], bers[949] # 5% and 95% confidence intervals
        return bers[4], bers[94] # 5% and 95% confidence intervals


