import car
import cv2
import numpy as np
import os
import serial
import socket
import threading
import time

from imutils.object_detection import non_max_suppression
from keras.layers import Dense, Activation
from keras.models import Sequential
import keras.models

dir_log = []
SIGMA = 0.33
stop_classifier = cv2.CascadeClassifier('cascade_xml/stop_sign_pjy.xml')
timestr = time.strftime('%Y%m%d_%H%M%S')


class RCDriver(object):

    def steer(self, prediction):

        # FORWARD
        if np.all(prediction   == [ 0., 0., 1.]):
            car.forward(100)
            car.pause(300)
            dir_log.append('Forward')
            print 'Forward'

        # FORWARD-LEFT
        elif np.all(prediction == [ 1., 0., 0.]):
            car.left(300)
            car.forward_left(200)
            car.left(700)
            car.pause(200)
            dir_log.append('Left')
            print 'Left'

        # FORWARD-RIGHT
        elif np.all(prediction == [ 0., 1., 0.]):
            car.right(300)
            car.forward_right(200)
            car.right(700)
            car.pause(200)
            dir_log.append('Right')
            print 'Right'

    def stop(self):
        print '* * * STOPPING! * * *'
        car.pause(5000)


rcdriver = RCDriver()


class ObjectDetection(object):

    global rcdriver
    global stop_classifier

    def detect(self, cascade_classifier, gray_image, image):

        # STOP SIGN
        stop_sign_detected = cascade_classifier.detectMultiScale(
            gray_image,
            scaleFactor=1.1,
            minNeighbors=10,
            minSize=(35, 35),
            maxSize=(45, 45))

        # Draw a rectangle around stop sign
        for (x_pos, y_pos, width, height) in stop_sign_detected:
            cv2.rectangle(image, (x_pos+5, y_pos+5), (x_pos+width-5, y_pos+height-5), (0, 0, 255), 2)
            cv2.putText(image, 'STOP SIGN', (x_pos, y_pos-10), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 2)

        # Execute the full stop
        if np.any(stop_sign_detected):
            rcdriver.stop()


        # PEDESTRIAN
        hog = cv2.HOGDescriptor()
        hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        orig = image.copy()

    	# Look for predestrians in the image
    	(rects, weights) = hog.detectMultiScale(image, winStride=(4, 4),
    		padding=(8, 8), scale=1.05)

    	# Draw the ORIGINAL bounding boxes
    	for (x, y, w, h) in rects:
    		cv2.rectangle(orig, (x, y), (x + w, y + h), (0, 0, 255), 2)

    	# Apply 'non-maxima suppression' to the bounding boxes using a fairly large overlap threshold to try to maintain overlapping
    	# boxes that are still people
    	rects = np.array([[x, y, x + w, y + h] for (x, y, w, h) in rects])
    	pick = non_max_suppression(rects, probs=None, overlapThresh=0.65)

    	# Draw the FINAL bounding boxes
    	for (xA, yA, xB, yB) in pick:
            cv2.rectangle(image, (xA, yA), (xB, yB), (0, 255, 0), 2)
            cv2.putText(image, 'PEDESTRIAN', (xA, yA-10), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 2)

obj_detection = ObjectDetection()


class TrustButVerify(object):

    global dir_log

    def __init__(self):
        # Arbitrarily designating a 'corner' as some % of width from either edge (e.g. 15%)
        self.corner_pct = .15


    def scan_for_signal(self, filtered_img):
        # Lower Left and Right corners
        last_row = filtered_img[-1]

        img_total_width  = len(last_row)
        img_corner_width = img_total_width * self.corner_pct

        left_corner  = last_row[  : img_corner_width + 1]
        right_corner = last_row[ -img_corner_width : ]

        # GOAL: Need a sum of 255 in either corner, which means at least the edge of a lane marker is visible in a corner
        # If either corner < 255, then ctrl-z mode
        if sum(left_corner) < 255 or sum(right_corner) < 255:
            return False


    def ctrl_z(self):

        print '< < < CTRL-Z MODE ACTIVATED! > > >'

        last_dir = dir_log[-1]

        # Forward -> Reverse
        if last_dir == 'Forward':
            car.reverse(100)
            car.pause(300)
            print '< REVERSE >'

        # Left -> Right
        elif last_dir == 'Left':
            car.right(300)
            car.reverse_right(200)
            car.right(700)
            car.pause(200)
            print '< REVERSE-RIGHT >'

        # Right -> Left
        elif last_dir == 'Right':
            car.left(300)
            car.reverse_left(200)
            car.left(700)
            car.pause(200)
            print '< REVERSE-LEFT >'

        # Then, where it previously made the wrong decision, go with the second argmax (assumes the same prediction probs will be calculated)


        # Finally, go back to normal neural net prediction

TBV = TrustButVerify()



class NeuralNetwork(object):

    global stop_classifier
    global timestr

    def __init__(self, receiving=False, piVideoObject=None):
        self.ctrl_z_mode = False
        self.receiving = receiving
        self.model = keras.models.load_model('nn_h5/nn.h5')

        # PiVideoStream class object is now here.
        self.piVideoObject = piVideoObject
        self.rcdriver = RCDriver()

        print 'NeuralNetwork_init OK'

        self.fetch()


    def auto_canny(self, blurred):
        # Compute the median of the single channel pixel intensities
        global SIGMA
        v = np.median(blurred)

        # Apply automatic Canny edge detection using the computed median of the image
        lower = int(max(0,   (1.0 - SIGMA) * v))
        upper = int(min(255, (1.0 + SIGMA) * v))
        edged = cv2.Canny(blurred, lower, upper)
        return edged


    def preprocess(self, frame):
        image_array = frame.reshape(1, 38400).astype(np.float32)
        image_array = image_array / 255.
        return image_array


    def predict(self, image):
        image_array = self.preprocess(image)
        y_hat       = self.model.predict(image_array)
        i_max       = np.argmax(y_hat)
        y_hat_final = np.zeros((1,3))
        np.put(y_hat_final, i_max, 1)
        return y_hat_final[0], y_hat


    def predict_second_best(self, image):
        image_array  = self.preprocess(image)
        y_hat        = self.model.predict(image_array)
        i_max_second = np.argsort(y_hat)[::-1][1]
        y_hat_final  = np.zeros((1,3))
        np.put(y_hat_final, i_max_second, 1)
        return y_hat_final[0], y_hat



    def fetch(self):

        frame = 0
        print 'good fetch1'

        while self.receiving:

            print 'good fetch2'

            # There's a chance that the Main thread can get to this point before the New thread begins streaming images.
            # To account for this, we create the jpg variable but set to None, and keep checking until it actually has something.
            jpg = None
            while jpg is None:
                jpg = self.piVideoObject.frame

            print 'good fetch3'

            gray  = cv2.imdecode(np.fromstring(jpg, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            image = cv2.imdecode(np.fromstring(jpg, dtype=np.uint8), cv2.IMREAD_UNCHANGED)

            print 'good fetch4'

            # Object detection
            obj_detection.detect(stop_classifier, gray, image)

            print 'good fetch5'

            # Lower half of the grayscale image
            roi = gray[120:240, :]

            # Apply GuassianBlur (reduces noise)
            blurred = cv2.GaussianBlur(roi, (3, 3), 0)

            # Apply Canny filter
            auto = self.auto_canny(blurred)

            # Show streaming images
            cv2.imshow('Original', image)
            cv2.imshow('What the model sees', auto)



            # *** NEW FEATURE ***
            print 'Trust but verify'
            # Check for signal in lower corners of image (boolean)
            if not TBV.scan_for_signal(auto):

                # TBV.ctrl_z() takes them back one step
                TBV.ctrl_z()
                self.ctrl_z_mode = True
                continue                        # return to top of while loop to get a fresh jpg

            # If TBV.scan_for_signal returned False, ctrl_z_mode is now True. Proceed with model's second best prediction.
            if self.ctrl_z_mode:
                prediction, probas = self.predict_second_best(auto)
                self.ctrl_z_mode = False

            # If TBV.scan_for_signal returned True, then all is well, and model makes prediciton for argmax.
            # prediction = self.model.predict(auto)
            else:
                prediction, probas = self.predict(auto)

            # Save frame and prediction record for debugging research
            prediction_english = None
            prediction_english_proba = None

            proba_left, proba_right, proba_forward = probas[0]

            if np.all(prediction   == [ 0., 0., 1.]):
                prediction_english = 'FORWARD'
                prediction_english_proba = proba_forward

            elif np.all(prediction == [ 1., 0., 0.]):
                prediction_english = 'LEFT'
                prediction_english_proba = proba_left

            elif np.all(prediction == [ 0., 1., 0.]):
                prediction_english = 'RIGHT'
                prediction_english_proba = proba_right

            cv2.putText(gray, "Prediction (sig={}): {}, {:>05}".format(SIGMA, prediction_english, prediction_english_proba), (10, 30), cv2.FONT_HERSHEY_SIMPLEX, .45, (255, 255, 0), 1)
            cv2.imwrite('test_frames_temp/frame{:>05}.jpg'.format(frame), gray)
            frame += 1

            # Send prediction to driver to tell it how to steer
            self.rcdriver.steer(prediction)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                self.stop()
                cv2.destroyAllWindows()



class PiVideoStream(object):

    def __init__(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # self.server_socket.bind(('192.168.1.66', 8000)) # The IP address of your computer (Paul's MacBook Air). This script should run before the one on the Pi.
        self.server_socket.bind(('10.10.10.2', 8000)) # The IP address of your computer (Paul's MacBook Air). This script should run before the one on the Pi.


        print 'Listening...'
        self.server_socket.listen(0)

        # Accept a single connection ('rb' is 'read binary')
        self.connection = self.server_socket.accept()[0].makefile('rb')

        # initialize the frame and the variable used to indicate
        # if the thread should be stopped
        self.frame = None
        self.stopped = False
        self.stream_bytes = ' '

        self.start()


    def start(self):
    	# start the thread to read frames from the video stream
        print 'Starting PiVideoStream thread...'
        print ' \"Hold on to your butts!\" '

        # Start a new thread
        t = threading.Thread(target=self.update, args=())
        t.daemon=True
        t.start()
        print '...thread running'

        # Main thread diverges from the new thread and activates the neural_network
        # The piVideoObject argument ('self') passes the PiVideoStream class object to NeuralNetwork.
        NeuralNetwork(receiving=True, piVideoObject=self)


    def update(self):
        while True:
            self.stream_bytes += self.connection.read(1024)
            first = self.stream_bytes.find('\xff\xd8')
            last = self.stream_bytes.find('\xff\xd9')
            if first != -1 and last != -1:
                self.frame = self.stream_bytes[first:last + 2]
                self.stream_bytes = self.stream_bytes[last + 2:]

		# if the thread indicator variable is set, stop the thread
		# and resource camera resources
		if self.stopped:
			self.connection.close()
			return


	def read(self):
		# return the frame most recently read
		return self.frame


	def stop(self):
		# indicate that the thread should be stopped
		self.stopped = True



if __name__ == '__main__':
    try:
        # Create an instance of PiVideoStream class
        video_stream = PiVideoStream()

    except (KeyboardInterrupt):
        # Rename the folder that collected all of the test frames. Then make a new folder to collect next round of test frames.
        os.rename(  './test_frames_temp', './test_frames_SAVED/test_frames_{}'.format(timestr))
        os.makedirs('./test_frames_temp')
        print '\nTerminating\n'
        car.pause(10000)
        video_stream.stop()
        print '\n! Received keyboard interrupt, quitting threads.\n'

    finally:
        video_stream.connection.close()
        print '...done.\n'